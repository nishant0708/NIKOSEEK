"""Company career sites / ATS portals (Workday, Greenhouse, Lever, iCIMS, Taleo, Oracle, SuccessFactors, ...).

LinkedIn / Indeed often hand the applicant over to the company's own portal, and many of those portals
(Workday above all) want an account per company: HP and NVIDIA are separate Workday tenants with
separate logins. This module:
  * recognises the portal + company ("tenant") from the URL            detect_ats()
  * walks Workday's start screens: Apply -> Apply Manually / Use My Last Application, cookie banner
                                                                         ats_prepare()
  * signs up with the job-portal email + password from the Profile page the first time, remembers
    agent_config["ats_accounts"][vendor][tenant] = True, and signs in on the next application
                                                                         ensure_signed_in()
  * pauses for a CAPTCHA / email verification and resumes by itself once it's done
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

from app.db.models import User
from app.services.agent_assist import clear_human_checks, wait_for_human
from app.services.agent_config import ats_account_known, get_ats_login, mark_ats_account
from app.services.form_filler import _click

logger = logging.getLogger(__name__)

VENDOR_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("workday", re.compile(r"https?://([a-z0-9-]+)\.wd\d+\.myworkday(?:jobs|site)\.com", re.I)),
    ("workday", re.compile(r"myworkdaysite\.com/(?:[a-z]{2}-[a-z]{2}/)?recruiting/([a-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)", re.I)),
    ("icims", re.compile(r"https?://(?:careers-)?([a-z0-9-]+)\.icims\.com", re.I)),
    ("taleo", re.compile(r"https?://([a-z0-9-]+)\.taleo\.net", re.I)),
    ("oracle", re.compile(r"https?://([a-z0-9-]+)\.fa\.[a-z0-9-]+\.oraclecloud\.com", re.I)),
]
# Portals whose application is a multi-page wizard (the agent presses Next / Save and Continue itself).
STEPPED_VENDORS = {"workday", "icims", "taleo", "oracle", "successfactors"}

EXISTS_PATTERN = re.compile(r"already (exists|in use|registered|been used|associated|taken)|account (already )?exists|"
                            r"email address is already|user ?name is (already )?taken", re.I)
VERIFY_PATTERN = re.compile(r"verif(y|ication)[^.\n]{0,60}(email|account|link|inbox)|check your (email|inbox)|"
                            r"(we('ve| have)? )?sent (you )?(an|a verification|a confirmation) (e-?mail|link)|activate your account", re.I)
BAD_LOGIN_PATTERN = re.compile(r"(wrong|incorrect|invalid)[^.\n]{0,40}(password|e-?mail|credentials|sign ?in|user)|not recogni[sz]ed|"
                               r"doesn'?t match|could not sign you in|no account|account (is )?locked", re.I)
PASSWORD_RULE_PATTERN = re.compile(r"password (must|should|requires|does not meet|is too)|at least \d+ characters|"
                                   r"(one|1) (special|uppercase|lowercase|numeric)|password requirements", re.I)

AUTH_JS = r"""() => {
  const vis = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const pw = [...document.querySelectorAll('input[type="password"]')].filter(vis);
  const text = (document.body.innerText || '').replace(/\s+/g, ' ');
  const clickable = [...document.querySelectorAll('button, a, [role="button"], [role="link"], input[type="submit"]')].filter(vis)
    .map(b => ((b.innerText || b.value || '') + ' ' + (b.getAttribute('aria-label') || '')).trim()).filter(Boolean);
  return { passwords: pw.length, text: text.slice(0, 3000), clickable: clickable.slice(0, 80) };
}"""


def detect_ats(url: str) -> tuple[str, str] | None:
    """(vendor, tenant) for a company portal URL, e.g. ("workday", "hp"), or None."""
    url = url or ""
    for vendor, pattern in VENDOR_PATTERNS:
        match = pattern.search(url)
        if match:
            return vendor, match.group(1).lower()
    parsed = urlparse(url)
    if "successfactors" in (parsed.hostname or ""):
        company = (parse_qs(parsed.query).get("company") or [""])[0]
        return "successfactors", (company or parsed.hostname or "").lower()
    return None


def portal_key(url: str) -> tuple[str, str]:
    """(vendor, tenant) — unknown portals are keyed by their host (e.g. ("site", "careers.acme.com"))."""
    found = detect_ats(url)
    if found:
        return found
    host = (urlparse(url or "").hostname or "unknown").lower()
    return "site", host.removeprefix("www.")


def is_stepped_portal(url: str) -> bool:
    found = detect_ats(url)
    return bool(found and found[0] in STEPPED_VENDORS)


async def _visible(locator) -> bool:
    try:
        return await locator.count() > 0 and await locator.first.is_visible()
    except Exception:
        return False


async def _first_visible(frame, selectors: list[str]):
    for selector in selectors:
        loc = frame.locator(selector)
        try:
            for i in range(min(await loc.count(), 8)):
                if await loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:
            continue
    return None


async def _button_by_text(frame, pattern: re.Pattern):
    buttons = frame.locator('button, a, [role="button"], [role="link"], input[type="submit"]')
    try:
        count = min(await buttons.count(), 120)
    except Exception:
        return None
    for i in range(count):
        button = buttons.nth(i)
        try:
            if not await button.is_visible():
                continue
            text = ((await button.inner_text()) or (await button.get_attribute("value") or "") or (await button.get_attribute("aria-label") or "")).strip()
        except Exception:
            continue
        if pattern.search(text):
            return button
    return None


# ---------------------------------------------------------------------------
# Start screens
# ---------------------------------------------------------------------------
# Workday's "Start Your Application" box. Its options, by data-automation-id (text as fallback).
START_OPTIONS = {
    "last": ('[data-automation-id="useMyLastApplication"]', re.compile(r"^\s*use my last application\s*$", re.I), "Use My Last Application"),
    "resume": ('[data-automation-id="autofillWithResume"]', re.compile(r"^\s*autofill with resume\s*$", re.I), "Autofill with Resume"),
    "manual": ('[data-automation-id="applyManually"]', re.compile(r"^\s*apply manually\s*$", re.I), "Apply Manually"),
}
WORKDAY_READY = ('[data-automation-id="adventureButton"], [data-automation-id="applyManually"], [data-automation-id="autofillWithResume"], '
                 '[data-automation-id="useMyLastApplication"], [data-automation-id="email"], [data-automation-id="file-upload-input-ref"], '
                 '[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"], input[type="password"]')
# A click that does not change the page is retried at most this often (the old code clicked Apply 12 times).
MAX_SAME_CLICK = 2
_clicks: dict[int, tuple[str, str, int]] = {}  # id(page) -> (url, action, times)


def _again(page, action: str) -> bool:
    """True if `action` may run (again) on this page; counts repeats on the same URL."""
    url, last, times = _clicks.get(id(page), ("", "", 0))
    times = times + 1 if (url == page.url and last == action) else 1
    _clicks[id(page)] = (page.url, action, times)
    return times <= MAX_SAME_CLICK


async def _start_option(frame, key: str):
    css, pattern, _ = START_OPTIONS[key]
    return await _first_visible(frame, [css]) or await _button_by_text(frame, pattern)


def _start_order(user: User, vendor: str, tenant: str, has_resume: bool) -> list[str]:
    """Which "Start Your Application" option to take:
    * this company already knows you (ats_accounts[workday][tenant]) -> Use My Last Application: everything you
      entered there last time comes back pre-filled
    * otherwise Autofill with Resume (Workday reads your CV into the form), then Apply Manually as the fallback."""
    order = ["last"] if ats_account_known(user, vendor, tenant) else []
    order += ["resume"] if has_resume else []
    return order + ["manual", "resume", "last"]


async def _upload_resume_step(page, log) -> bool:
    """Workday's "Autofill with Resume" page: only a file drop zone + Continue. Upload the CV and continue."""
    from app.services.form_filler import upload_resume

    frame = page.main_frame
    drop = await _first_visible(frame, ['[data-automation-id="file-upload-drop-zone"]', '[data-automation-id="select-files"]'])
    file_input = frame.locator('input[type="file"]')
    if await file_input.count() == 0 or (drop is None and not await _visible(frame.locator('[data-automation-id="file-upload-input-ref"]'))):
        return False
    texts = await frame.locator('input[type="text"]:visible, input:not([type]):visible, textarea:visible').count()
    nxt = await _first_visible(frame, ['[data-automation-id="pageFooterNextButton"]', '[data-automation-id="bottom-navigation-next-button"]'])
    if texts > 1 or nxt is None:
        return False  # a real form page with a resume field: the form filler handles it
    application = getattr(log, "application", None)
    uploaded = await _visible(frame.locator('[data-automation-id="file-upload-successful"], [data-automation-id="delete-file"]'))
    if not uploaded:
        if not _again(page, "upload"):
            return False
        name = await upload_resume(frame, getattr(application, "resume_key", None), application.user.username if application else "applicant")
        if not name:
            return False
        await log("action", f"Uploaded your resume ({name}) for Workday's autofill.", data={"url": page.url})
        for _ in range(20):  # Workday parses the CV before Continue works
            await page.wait_for_timeout(1000)
            if await _visible(frame.locator('[data-automation-id="file-upload-successful"], [data-automation-id="delete-file"]')):
                break
    if not _again(page, "continue-after-upload"):
        return False
    await _click(nxt)
    await page.wait_for_timeout(3000)
    await log("action", "Continued — Workday pre-fills the application from your resume.", data={"url": page.url})
    return True


WIZARD_MARKERS = ('[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"], '
                  '[data-automation-id="progressBar"], [data-automation-id="progressBarActiveStep"]')


async def in_application_wizard(page) -> bool:
    """Workday's application has started (URL .../apply/..., a Continue/Next footer or progress bar, no sign-in form).
    From here every page — resume upload, My Information, questions, Review — is handled by the page-by-page filler,
    so the navigator never has to ask the AI (or you) what to click."""
    vendor, _ = portal_key(page.url)
    if vendor != "workday" or "/apply" not in (page.url or ""):
        return False
    if await _auth_kind(page) is not None:
        return False
    return await _visible(page.main_frame.locator(WIZARD_MARKERS))


async def ats_prepare(page, user: User, log) -> bool:
    """One step through a portal's start screens. True if it did something (the caller then re-checks the page).
    Workday: cookie notice -> Start Your Application choice (it opens ON TOP of the job page, whose Apply button
    stays visible, so the choice is checked BEFORE Apply) -> Apply -> resume upload page."""
    vendor, tenant = portal_key(page.url)
    frame = page.main_frame
    if vendor == "workday":
        try:  # Workday renders with JavaScript: wait until it has drawn something we know
            await frame.wait_for_selector(WORKDAY_READY, state="visible", timeout=12000)
        except Exception:
            pass
        cookie = await _first_visible(frame, ['[data-automation-id="legalNoticeAcceptButton"]'])
        if cookie is not None and _again(page, "cookie"):
            await _click(cookie)
            await page.wait_for_timeout(1500)
            await log("action", "Accepted the cookie notice.", data={"url": page.url})
            return True
        options = {key: await _start_option(frame, key) for key in START_OPTIONS}
        if any(options.values()):
            application = getattr(log, "application", None)
            for key in _start_order(user, vendor, tenant, bool(getattr(application, "resume_key", None))):
                if options.get(key) is not None and _again(page, f"start:{key}"):
                    label = START_OPTIONS[key][2]
                    reason = {"last": f"you already have an account at {tenant}, so your last application is reused",
                              "resume": "Workday fills the form from your resume", "manual": "fallback"}[key]
                    await _click(options[key])
                    await page.wait_for_timeout(3000)
                    await log("action", f"Chose “{label}” on Workday's start screen ({reason}).", data={"url": page.url, "portal": f"{vendor}:{tenant}"})
                    return True
            return False
        if await _upload_resume_step(page, log):
            return True
        apply = await _first_visible(frame, ['[data-automation-id="adventureButton"]'])
        if apply is not None and _again(page, "apply"):
            await _click(apply)
            await page.wait_for_timeout(2500)
            await log("action", "Clicked Apply on the Workday job page.", data={"url": page.url, "portal": f"{vendor}:{tenant}"})
            return True
    email_first = await _first_visible(frame, ['[data-automation-id="SignInWithEmailButton"]'])
    if email_first is None and await _auth_kind(page) is None:
        email_first = await _button_by_text(frame, re.compile(r"^\s*(sign in|log ?in|continue) with e-?mail\s*$", re.I))
    if email_first is not None:
        await _click(email_first)
        await page.wait_for_timeout(2000)
        await log("action", "Chose “Sign in with email”.", data={"url": page.url})
        return True
    return False


# ---------------------------------------------------------------------------
# Sign up / sign in
# ---------------------------------------------------------------------------
async def _auth_kind(page) -> str | None:
    """"signup" (password + confirm), "signin" (one password field) or None."""
    try:
        info = await page.main_frame.evaluate(AUTH_JS)
    except Exception:
        return None
    if info["passwords"] >= 2:
        return "signup"
    if info["passwords"] == 1:
        return "signin"
    return None


async def _page_text(page) -> str:
    try:
        return (await page.main_frame.evaluate(AUTH_JS))["text"]
    except Exception:
        return ""


EMAIL_SELECTORS = ['input[data-automation-id="email"]', 'input[type="email"]', 'input[autocomplete="username"]', 'input[autocomplete="email"]',
                   'input[name*="email" i]', 'input[id*="email" i]', 'input[name*="user" i]', 'input[id*="user" i]']
PASSWORD_SELECTORS = ['input[data-automation-id="password"]', 'input[type="password"]']
VERIFY_SELECTORS = ['input[data-automation-id="verifyPassword"]']
SIGNUP_SUBMIT = ['[data-automation-id="createAccountSubmitButton"]']
SIGNIN_SUBMIT = ['[data-automation-id="signInSubmitButton"]']
TO_SIGNUP = ['[data-automation-id="createAccountLink"]']
TO_SIGNIN = ['[data-automation-id="signInLink"]']
SIGNUP_TEXT = re.compile(r"^\s*(create (an )?account|sign ?up|register|create account and continue)\s*$", re.I)
SIGNIN_TEXT = re.compile(r"^\s*(sign ?in|log ?in|login|continue)\s*$", re.I)
CONSENT_LABEL = re.compile(r"agree|terms|privacy|consent|acknowledge|accept", re.I)


async def _fill_credentials(page, email: str, password: str, signup: bool, log) -> None:
    frame = page.main_frame
    email_box = await _first_visible(frame, EMAIL_SELECTORS)
    if email_box is None:
        # Fall back to the visible text input right before the password field.
        email_box = await _first_visible(frame, ['input[type="text"]'])
    if email_box is None:
        raise RuntimeError("Could not find the email field on the sign-in page.")
    await email_box.fill(email)
    passwords = frame.locator('input[type="password"]:visible')
    first = await _first_visible(frame, PASSWORD_SELECTORS)
    if first is None:
        raise RuntimeError("Could not find the password field.")
    await first.fill(password)
    if signup:
        verify = await _first_visible(frame, VERIFY_SELECTORS)
        if verify is None and await passwords.count() >= 2:
            verify = passwords.nth(1)
        if verify is not None:
            await verify.fill(password)
        # The account-creation agreement (Workday: createAccountCheckbox) — required to create the account you asked for.
        boxes = frame.locator('input[data-automation-id="createAccountCheckbox"], input[type="checkbox"]')
        for i in range(min(await boxes.count(), 6)):
            box = boxes.nth(i)
            try:
                if await box.is_checked():
                    continue
                label = await box.evaluate("b => ((b.labels && b.labels[0] && b.labels[0].innerText) || b.getAttribute('aria-label') || "
                                           "(b.closest('label, div') || {}).innerText || '').slice(0, 200)")
                auto_id = await box.get_attribute("data-automation-id") or ""
                if auto_id == "createAccountCheckbox" or CONSENT_LABEL.search(label or ""):
                    await box.check(force=True, timeout=3000)
                    await log("action", f"Ticked the account-creation agreement: “{(label or 'terms').strip()[:120]}”.")
            except Exception:
                continue


async def _submit(page, signup: bool) -> bool:
    frame = page.main_frame
    button = await _first_visible(frame, SIGNUP_SUBMIT if signup else SIGNIN_SUBMIT)
    if button is None:
        button = await _button_by_text(frame, SIGNUP_TEXT if signup else SIGNIN_TEXT)
    if button is None:
        try:
            await frame.locator('input[type="password"]:visible').last.press("Enter")
            return True
        except Exception:
            return False
    await _click(button)
    return True


async def _switch(page, to_signup: bool) -> bool:
    frame = page.main_frame
    link = await _first_visible(frame, TO_SIGNUP if to_signup else TO_SIGNIN)
    if link is None:
        pattern = re.compile(r"^\s*(create (an )?account|sign ?up|register|new user)\b", re.I) if to_signup else \
            re.compile(r"^\s*(already have an account\??\s*)?(sign ?in|log ?in)\s*$", re.I)
        link = await _button_by_text(frame, pattern)
    if link is None:
        return False
    await _click(link)
    await page.wait_for_timeout(2000)
    return True


async def ensure_signed_in(page, user: User, log) -> bool:
    """If the tab shows a sign-in / create-account form, get past it. True if it handled one (the page changed)."""
    kind = await _auth_kind(page)
    if kind is None:
        return False
    vendor, tenant = portal_key(page.url)
    portal = f"{vendor}:{tenant}"
    await log("info", f"{tenant} ({vendor}) asks for an account — {'creating one' if kind == 'signup' else 'signing in'} with your job-portal login.",
              data={"url": page.url, "portal": portal, "form": kind})

    creds = get_ats_login(user)
    if creds is None:
        async def handled() -> bool:
            _refresh_user(user)
            return get_ats_login(user) is not None or await _auth_kind(page) is None

        answer = await wait_for_human(
            log, f"{tenant} needs an account. Add your job-portal email + password on the Profile page (“Company job portals”), "
                 "then press Continue — or sign in / sign up yourself in the browser window.",
            handled, options=[{"id": 0, "label": "Continue"}], reason="job-portal login missing")
        if answer is None:
            raise RuntimeError("No job-portal login was provided.")
        _refresh_user(user)
        creds = get_ats_login(user)
        if creds is None:
            return await _auth_kind(page) is None  # you signed in yourself
    email, password = creds

    tried_signup = tried_signin = False
    for _ in range(6):
        await clear_human_checks(page, log, "on the sign-in page")
        kind = await _auth_kind(page)
        if kind is None:
            mark_ats_account(user, vendor, tenant)
            await log("info", f"Signed in to {tenant} ({vendor}). Saved: next time I'll sign in directly.", data={"portal": portal})
            return True
        if kind == "signin" and not tried_signup and not tried_signin and not ats_account_known(user, vendor, tenant):
            # First time at this company: create the account (if the page offers it).
            if await _switch(page, to_signup=True):
                continue
        signup = kind == "signup"
        await _fill_credentials(page, email, password, signup, log)
        await clear_human_checks(page, log, "before signing in")
        if not await _submit(page, signup):
            break
        tried_signup |= signup
        tried_signin |= not signup
        await log("action", f"Sent the {'create-account' if signup else 'sign-in'} form on {tenant} ({vendor}) with {email}.", data={"portal": portal})
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(3500)
        if await _auth_kind(page) is None:
            continue  # success -> recorded at the top of the loop
        text = await _page_text(page)
        if signup and EXISTS_PATTERN.search(text):
            await log("info", f"{tenant} already has an account for {email} — signing in instead.")
            mark_ats_account(user, vendor, tenant)
            await _switch(page, to_signup=False)
            continue
        if signup and PASSWORD_RULE_PATTERN.search(text):
            raise RuntimeError(f"{tenant} rejected your job-portal password: {PASSWORD_RULE_PATTERN.search(text).group(0)}… "
                               "Use a stronger one on the Profile page (8+ chars, upper + lower case, a digit and a symbol).")
        if VERIFY_PATTERN.search(text):
            mark_ats_account(user, vendor, tenant)  # the account exists now, it just needs the email link
            answer = await wait_for_human(
                log, f"{tenant} sent a verification email to {email}. Open it and click the link, then press “Verified”. "
                     "(If the link signs you in here, I'll notice and continue.)",
                _gone(page), options=[{"id": 0, "label": "Verified — sign in"}], reason="email verification")
            if answer is None:
                raise RuntimeError("The account email was not verified in time.")
            if await _auth_kind(page) is not None and not await _switch(page, to_signup=False):
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
            continue
        if not signup and BAD_LOGIN_PATTERN.search(text) and not tried_signup:
            if await _switch(page, to_signup=True):
                continue
        break

    if await _auth_kind(page) is None:
        mark_ats_account(user, vendor, tenant)
        return True
    # Could not get in automatically: you finish it in the window, the agent continues by itself.
    answer = await wait_for_human(
        log, f"I couldn't sign in to {tenant} automatically. Please sign in (or create the account) in the browser window — "
             "I'll continue as soon as the sign-in page is gone.",
        _gone(page), reason="sign-in failed")
    if answer is None:
        raise RuntimeError(f"Could not sign in to {tenant}.")
    if await _auth_kind(page) is None:
        mark_ats_account(user, vendor, tenant)
    return True


def _gone(page):
    async def check() -> bool:
        return await _auth_kind(page) is None
    return check


def _refresh_user(user: User) -> None:
    from sqlalchemy.orm import object_session

    db = object_session(user)
    if db is not None:
        try:
            db.refresh(user, attribute_names=["agent_config"])
        except Exception:
            pass
