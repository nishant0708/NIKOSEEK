"""Persistent, user-controlled Indeed browser sessions.

This is intentionally separate from the LinkedIn browser manager: each source
gets its own Chrome profile, cookies, connection API, and saved credentials.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import BrowserSession, User
from app.services.agent_config import indeed_public_payload
from app.services.linkedin_browser import LOGIN_IN_PROGRESS, NEEDS_LOGIN, READY, get_browser_session, mark_browser_session

logger = logging.getLogger(__name__)
SOURCE = "indeed"
PROFILE_ROOT = Path(__file__).resolve().parents[2] / ".browser_profiles"
# `auth` is Indeed's current email-first entry point. The older
# `/account/login` path can land on a regional interstitial without an email
# input, which made the saved address appear not to be found.
LOGIN_URL = "https://secure.indeed.com/auth?hl=en&continue=%2F"
HOME_URL = "https://www.indeed.com/"
LOGIN_TIMEOUT_SECONDS = 300
AUTH_URL_PATTERN = re.compile(r"indeed\.com/(?:account/login|login|auth|secure/)", re.I)
LOGGED_OUT_MARKERS = 'a[href*="account/login"], [data-testid="header-signin-link"]'
USERNAME_SELECTOR = 'input[type="email"], input[name="email"], input[id*="email" i], input[autocomplete="email"], input[autocomplete="username"]'
PASSWORD_SELECTOR = 'input[type="password"], input[autocomplete="current-password"]'
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-default-browser-check", "--no-first-run"]


class IndeedAuthRequired(RuntimeError):
    pass


def session_payload(session: BrowserSession) -> dict:
    return {"source": session.source, "status": session.status, "connected": session.status == READY,
            "last_login_at": session.last_login_at, "last_checked_at": session.last_checked_at, "error": session.error}


def is_auth_url(url: str) -> bool:
    return bool(AUTH_URL_PATTERN.search(url or ""))


async def has_session_cookie(context) -> bool:
    cookies = await context.cookies("https://www.indeed.com")
    # Indeed's cookie names vary by region, but authenticated sessions retain at
    # least one account/session cookie. URL/DOM checks below provide the guard.
    return any(c["name"].lower() in {"ctk", "indeed_rcc", "indeed_session"} and c.get("value") for c in cookies)


async def page_looks_logged_out(page) -> bool:
    if "indeed.com" not in (page.url or ""):
        return False
    if is_auth_url(page.url):
        return True
    try:
        return await page.locator(LOGGED_OUT_MARKERS).count() > 0
    except Exception:
        return False


async def _start_passkey_login(page, user: User) -> None:
    """Start Indeed's email-first sign-in and hand WebAuthn to the user.

    A passkey assertion must be approved by the user in the native browser/OS
    prompt; it is deliberately never read, stored, or automated by this app.
    """
    try:
        email = str(indeed_public_payload(user).get("email") or "").strip()
        if not email:
            raise ValueError("No Indeed email is saved. Add it on the Profile page, then connect again.")
        email_input = page.locator(USERNAME_SELECTOR).first
        await email_input.wait_for(state="visible", timeout=10000)
        await email_input.fill(email)
        # Indeed changes the button markup by country; its accessible name is
        # stable enough to use first, with a submit-button fallback.
        continue_button = page.get_by_role("button", name=re.compile(r"continue|sign in|submit", re.I))
        if await continue_button.count() > 0:
            await continue_button.first.click()
        else:
            await page.locator('button[type="submit"], input[type="submit"]').first.click()
        await page.wait_for_timeout(800)
        passkey = page.get_by_role("button", name=re.compile(r"(?:use|sign in with).{0,20}passkey|passkey", re.I))
        if await passkey.count() > 0:
            await passkey.first.click()
    except Exception as exc:
        # The user can still complete the visible flow manually, but preserve
        # an actionable message in the backend log instead of silently failing.
        logger.warning("Indeed email/passkey start skipped (%s); waiting for manual login", str(exc).splitlines()[0][:120])


class IndeedBrowserManager:
    def __init__(self) -> None:
        self._playwright = None
        self._contexts: dict[int, object] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._start_lock = asyncio.Lock()
        self._connect_tasks: dict[int, asyncio.Task] = {}

    def lock_for(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    async def _ensure_playwright(self):
        async with self._start_lock:
            if self._playwright is None:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
        return self._playwright

    async def get_context(self, user_id: int, seed_state: dict | None = None):
        context = self._contexts.get(user_id)
        if context is not None:
            try:
                _ = context.pages
                return context
            except Exception:
                self._contexts.pop(user_id, None)
        playwright = await self._ensure_playwright()
        profile_dir = PROFILE_ROOT / f"user_{user_id}" / SOURCE
        first_launch = not profile_dir.exists()
        profile_dir.mkdir(parents=True, exist_ok=True)
        options = {"user_data_dir": str(profile_dir), "headless": False, "viewport": {"width": 1366, "height": 900}, "args": LAUNCH_ARGS, "ignore_default_args": ["--enable-automation"]}
        try:
            context = await playwright.chromium.launch_persistent_context(channel="chrome", **options)
        except Exception:
            context = await playwright.chromium.launch_persistent_context(**options)
        if first_launch and seed_state and seed_state.get("cookies"):
            await context.add_cookies(seed_state["cookies"])
        context.on("close", lambda _ctx=None, uid=user_id: self._contexts.pop(uid, None))
        self._contexts[user_id] = context
        return context

    async def ensure_logged_in(self, db: Session, user: User, page, *, on_wait=None, timeout_s: int = LOGIN_TIMEOUT_SECONDS) -> None:
        context = page.context
        session = get_browser_session(db, user, SOURCE)
        await page.wait_for_timeout(1200)
        if not await page_looks_logged_out(page):
            # A signed-in Indeed page is the authoritative check. Cookie names
            # are intentionally not relied on because they differ by locale.
            if session.status != READY:
                mark_browser_session(db, session, status=READY, auth_state=await context.storage_state())
            return
        mark_browser_session(db, session, status=LOGIN_IN_PROGRESS, error=None)
        if on_wait:
            await on_wait()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await page.bring_to_front()
        await _start_passkey_login(page, user)
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if page.is_closed():
                break
            if not await page_looks_logged_out(page):
                mark_browser_session(db, session, status=READY, auth_state=await context.storage_state())
                return
            await asyncio.sleep(1.5)
        mark_browser_session(db, session, status=NEEDS_LOGIN, error="Indeed login was not completed in the browser window within the time limit.")
        raise IndeedAuthRequired("Indeed login required. Open 'Connect Indeed' and finish signing in in the popup window.")

    async def _run_connect(self, user_id: int, session_factory) -> None:
        db = session_factory()
        try:
            user = db.get(User, user_id)
            if user is None:
                return
            session = get_browser_session(db, user, SOURCE)
            async with self.lock_for(user_id):
                try:
                    context = await self.get_context(user_id, session.auth_state)
                    page = await context.new_page()
                    await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
                    await self.ensure_logged_in(db, user, page)
                except IndeedAuthRequired:
                    pass
                except Exception as exc:
                    logger.exception("Indeed connect failed for user_id=%s", user_id)
                    mark_browser_session(db, session, status=NEEDS_LOGIN, error=str(exc)[:500])
        finally:
            db.close()
            self._connect_tasks.pop(user_id, None)

    def start_connect(self, db: Session, user: User, session_factory) -> BrowserSession:
        session = get_browser_session(db, user, SOURCE)
        task = self._connect_tasks.get(user.id)
        if task is None or task.done():
            mark_browser_session(db, session, status=LOGIN_IN_PROGRESS, error=None)
            self._connect_tasks[user.id] = asyncio.create_task(self._run_connect(user.id, session_factory))
        return session

    async def disconnect(self, db: Session, user: User) -> BrowserSession:
        session = get_browser_session(db, user, SOURCE)
        task = self._connect_tasks.pop(user.id, None)
        if task and not task.done(): task.cancel()
        context = self._contexts.pop(user.id, None)
        if context is not None:
            try:
                await context.clear_cookies(); await context.close()
            except Exception: pass
        mark_browser_session(db, session, status=NEEDS_LOGIN, error=None)
        session.auth_state = None
        db.commit()
        return session

    async def shutdown(self) -> None:
        for task in self._connect_tasks.values(): task.cancel()
        for context in self._contexts.values():
            try: await context.close()
            except Exception: pass
        self._contexts.clear()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


indeed_browser = IndeedBrowserManager()
