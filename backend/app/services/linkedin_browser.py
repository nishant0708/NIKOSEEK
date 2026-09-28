"""One long-lived, headed LinkedIn browser per user.

Why this exists
---------------
The old flow launched a brand-new Chromium for every application, tried to type the
password into /login and then closed the browser. That broke in three ways:
  * LinkedIn treats a fresh automated browser + password login as a bot -> checkpoint / CAPTCHA /
    "Could not locate LinkedIn login fields".
  * Nothing survived between runs, so every job re-triggered the login.
  * The filled form was destroyed by ``browser.close()`` before the human could review it.

Now each user gets a *persistent* Chrome profile on disk (like a normal browser profile).
The user logs in once through a visible window (``connect``), LinkedIn trusts that device,
and every later application opens a tab in that same window.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import BrowserSession, User
from app.services.agent_config import get_linkedin_login

logger = logging.getLogger(__name__)

SOURCE = "linkedin"
PROFILE_ROOT = Path(__file__).resolve().parents[2] / ".browser_profiles"
LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_TIMEOUT_SECONDS = 300  # time the user gets to finish 2FA / CAPTCHA in the popup
AUTH_URL_PATTERN = re.compile(r"linkedin\.com/(?:login|uas/|checkpoint|authwall|signup|reg/|signin)", re.I)
LOGGED_OUT_MARKERS = (
    '[data-tracking-control-name*="sign-in-modal"], .sign-up-modal__outlet, '
    'a[data-tracking-control-name*="nav-header-signin"], .authwall-join-form'
)
USERNAME_SELECTOR = "#username, input[name='session_key'], input[autocomplete='username']"
PASSWORD_SELECTOR = "#password, input[name='session_password'], input[type='password']"
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-default-browser-check", "--no-first-run"]

# Session statuses stored in browser_sessions.status
NEEDS_LOGIN = "NEEDS_LOGIN"
LOGIN_IN_PROGRESS = "LOGIN_IN_PROGRESS"
READY = "READY"


class LinkedInAuthRequired(RuntimeError):
    """Raised when the LinkedIn session is missing/expired and the user did not log in in time."""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_browser_session(db: Session, user: User, source: str = SOURCE) -> BrowserSession:
    session = db.query(BrowserSession).filter(BrowserSession.user_id == user.id, BrowserSession.source == source).one_or_none()
    if session is None:
        session = BrowserSession(user_id=user.id, source=source, status=NEEDS_LOGIN)
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


def mark_browser_session(db: Session, session: BrowserSession, *, status: str | None = None, auth_state: dict | None = None, error: str | None = None) -> None:
    if status is not None:
        session.status = status
    if auth_state is not None:
        session.auth_state = auth_state
        session.last_login_at = datetime.now(UTC)
    session.error = error
    session.last_checked_at = datetime.now(UTC)
    db.commit()


def session_payload(session: BrowserSession) -> dict:
    return {
        "source": session.source,
        "status": session.status,
        "connected": session.status == READY,
        "last_login_at": session.last_login_at,
        "last_checked_at": session.last_checked_at,
        "error": session.error,
    }


# ---------------------------------------------------------------------------
# Page / cookie checks
# ---------------------------------------------------------------------------
async def has_session_cookie(context) -> bool:
    """LinkedIn only sets `li_at` for authenticated members."""
    cookies = await context.cookies("https://www.linkedin.com")
    return any(cookie["name"] == "li_at" and cookie.get("value") for cookie in cookies)


def is_auth_url(url: str) -> bool:
    return bool(AUTH_URL_PATTERN.search(url or ""))


async def page_looks_logged_out(page) -> bool:
    """True for the guest job page (sign-in modal / nav 'Sign in' link) or any login/authwall URL."""
    if "linkedin.com" not in (page.url or ""):
        return False
    if is_auth_url(page.url):
        return True
    try:
        return await page.locator(LOGGED_OUT_MARKERS).count() > 0
    except Exception:
        return False


async def _autofill_credentials(page, user: User) -> bool:
    """Best effort: pre-fill saved credentials. Never fatal — the user can always type in the popup."""
    try:
        email, password = get_linkedin_login(user)
    except ValueError:
        return False
    try:
        # "Welcome back" account chooser -> switch to the classic form.
        other = page.get_by_role("button", name=re.compile(r"sign in using another account", re.I))
        if await other.count() > 0:
            await other.first.click()
        username = page.locator(USERNAME_SELECTOR).first
        await username.wait_for(state="visible", timeout=10000)
        await username.fill(email)
        password_input = page.locator(PASSWORD_SELECTOR).first
        await password_input.fill(password)
        await page.locator("button[type='submit']").first.click()
        logger.info("Pre-filled LinkedIn credentials; waiting for the member session")
        return True
    except Exception as exc:
        logger.info("LinkedIn autofill skipped (%s); waiting for manual login", str(exc).splitlines()[0][:120])
        return False


async def wait_for_login(context, page, timeout_s: int = LOGIN_TIMEOUT_SECONDS) -> bool:
    """Poll until LinkedIn issues li_at and the tab has left the login/checkpoint pages."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if page.is_closed():
            return await has_session_cookie(context)
        if await has_session_cookie(context) and not is_auth_url(page.url):
            return True
        await asyncio.sleep(1.5)
    return False


# ---------------------------------------------------------------------------
# Browser manager
# ---------------------------------------------------------------------------
class LinkedInBrowserManager:
    def __init__(self) -> None:
        self._playwright = None
        self._contexts: dict[int, object] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._start_lock = asyncio.Lock()
        self._connect_tasks: dict[int, asyncio.Task] = {}

    def lock_for(self, user_id: int) -> asyncio.Lock:
        """Serialises everything that drives one user's browser (connect + application runs)."""
        return self._locks.setdefault(user_id, asyncio.Lock())

    async def _ensure_playwright(self):
        async with self._start_lock:
            if self._playwright is None:
                try:
                    from playwright.async_api import async_playwright
                except Exception as exc:
                    raise RuntimeError("Playwright is not installed. Run `pip install playwright && playwright install chromium`.") from exc
                self._playwright = await async_playwright().start()
        return self._playwright

    async def get_context(self, user_id: int, seed_state: dict | None = None):
        """Return the user's live persistent context, launching the window if needed."""
        context = self._contexts.get(user_id)
        if context is not None:
            try:
                _ = context.pages  # raises if the browser died
                return context
            except Exception:
                self._contexts.pop(user_id, None)

        playwright = await self._ensure_playwright()
        profile_dir = PROFILE_ROOT / f"user_{user_id}" / SOURCE
        first_launch = not profile_dir.exists()
        profile_dir.mkdir(parents=True, exist_ok=True)
        options = {
            "user_data_dir": str(profile_dir),
            "headless": False,
            "viewport": {"width": 1366, "height": 900},
            "args": LAUNCH_ARGS,
            "ignore_default_args": ["--enable-automation"],
        }
        try:
            # Real Chrome is far less likely to hit LinkedIn's bot checks than bundled Chromium.
            context = await playwright.chromium.launch_persistent_context(channel="chrome", **options)
        except Exception as exc:
            logger.info("Google Chrome channel unavailable (%s); using bundled Chromium", str(exc).splitlines()[0][:120])
            context = await playwright.chromium.launch_persistent_context(**options)

        if first_launch and seed_state and seed_state.get("cookies"):
            # Carry over the cookies saved by the old storage_state flow.
            try:
                await context.add_cookies(seed_state["cookies"])
            except Exception:
                logger.warning("Could not seed stored LinkedIn cookies into the new profile")

        context.on("close", lambda _ctx=None, uid=user_id: self._contexts.pop(uid, None))
        self._contexts[user_id] = context
        return context

    async def new_page(self, context):
        """Reuse the blank tab Chrome opens on launch, otherwise open a new one."""
        for page in context.pages:
            if page.url in ("about:blank", "chrome://newtab/", ""):
                return page
        return await context.new_page()

    # ---- interactive login -------------------------------------------------
    async def ensure_logged_in(self, db: Session, user: User, page, *, on_wait=None, timeout_s: int = LOGIN_TIMEOUT_SECONDS) -> None:
        """Make sure `page`'s context is a LinkedIn member session; if not, let the user log in in that window."""
        context = page.context
        browser_session = get_browser_session(db, user)
        await page.wait_for_timeout(1200)  # let the guest sign-in modal render before judging
        if await has_session_cookie(context) and not await page_looks_logged_out(page):
            if browser_session.status != READY:
                mark_browser_session(db, browser_session, status=READY, auth_state=await context.storage_state())
            return

        mark_browser_session(db, browser_session, status=LOGIN_IN_PROGRESS, error=None)
        if on_wait is not None:
            await on_wait()
        return_url = page.url if "linkedin.com" in (page.url or "") and not is_auth_url(page.url) else None
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await page.bring_to_front()
        if not is_auth_url(page.url) and await has_session_cookie(context):
            ok = True  # /login bounced straight to the feed: already a member
        else:
            await _autofill_credentials(page, user)
            ok = await wait_for_login(context, page, timeout_s)

        if not ok:
            mark_browser_session(
                db, browser_session, status=NEEDS_LOGIN,
                error="LinkedIn login was not completed in the browser window within the time limit.",
            )
            raise LinkedInAuthRequired("LinkedIn login required. Open 'Connect LinkedIn' and finish signing in in the popup window.")

        mark_browser_session(db, browser_session, status=READY, auth_state=await context.storage_state())
        logger.info("LinkedIn session ready for user_id=%s", user.id)
        if return_url and not page.is_closed():
            await page.goto(return_url, wait_until="domcontentloaded", timeout=60000)

    async def _run_connect(self, user_id: int, session_factory) -> None:
        db: Session = session_factory()
        try:
            user = db.get(User, user_id)
            if user is None:
                return
            browser_session = get_browser_session(db, user)
            async with self.lock_for(user_id):
                try:
                    context = await self.get_context(user_id, browser_session.auth_state)
                    page = await self.new_page(context)
                    await page.goto(FEED_URL, wait_until="domcontentloaded", timeout=60000)
                    await self.ensure_logged_in(db, user, page)
                except LinkedInAuthRequired:
                    pass  # status + error already stored
                except Exception as exc:
                    logger.exception("LinkedIn connect failed for user_id=%s", user_id)
                    mark_browser_session(db, browser_session, status=NEEDS_LOGIN, error=str(exc)[:500])
        finally:
            db.close()
            self._connect_tasks.pop(user_id, None)

    def start_connect(self, db: Session, user: User, session_factory) -> BrowserSession:
        """Open the LinkedIn popup in the background; the frontend polls the session status."""
        browser_session = get_browser_session(db, user)
        task = self._connect_tasks.get(user.id)
        if task is None or task.done():
            mark_browser_session(db, browser_session, status=LOGIN_IN_PROGRESS, error=None)
            self._connect_tasks[user.id] = asyncio.create_task(self._run_connect(user.id, session_factory))
        return browser_session

    async def disconnect(self, db: Session, user: User) -> BrowserSession:
        browser_session = get_browser_session(db, user)
        task = self._connect_tasks.pop(user.id, None)
        if task is not None and not task.done():
            task.cancel()
        context = self._contexts.pop(user.id, None)
        if context is not None:
            try:
                await context.clear_cookies()
                await context.close()
            except Exception:
                pass
        mark_browser_session(db, browser_session, status=NEEDS_LOGIN, error=None)
        browser_session.auth_state = None
        db.commit()
        return browser_session

    async def shutdown(self) -> None:
        for task in list(self._connect_tasks.values()):
            task.cancel()
        for context in list(self._contexts.values()):
            try:
                await context.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


linkedin_browser = LinkedInBrowserManager()
