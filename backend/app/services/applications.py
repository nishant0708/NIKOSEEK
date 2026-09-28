from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.db.models import Application, BrowserSession, ScrapedJob, User
from app.services.agent_config import get_linkedin_login
from app.services.storage import list_resumes, read_details_markdown, read_profile_markdown
from app.ws.broker import broker

logger = logging.getLogger(__name__)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_NAV_STEPS = 8
MIN_FORM_FIELDS = 4  # a page with at least this many visible inputs (off LinkedIn) counts as "the form"
EASY_APPLY_PATTERN = re.compile(r"easy apply", re.I)
SUBMIT_PATTERN = re.compile(r"\bsubmit\b|send application", re.I)

# Radios/checkboxes (EEO, consent, etc.) are deliberately left for the human reviewer.
FIELD_LOCATOR = (
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"])'
    ':not([type="radio"]):not([type="checkbox"]), textarea, select'
)
# NOTE: single-needle entries must be 1-tuples. The old `("city")` was a plain string,
# so `any(needle in haystack for needle in "city")` iterated characters and matched almost anything.
FIELD_KEY_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("first name", "firstname", "given-name", "given_name"), "first_name"),
    (("last name", "lastname", "family-name", "surname", "family_name"), "last_name"),
    (("full name", "your name", "legal name"), "full_name"),
    (("email", "e-mail"), "email"),
    (("phone", "mobile", "tel"), "phone"),
    (("city",), "city"),
    (("country",), "country"),
    (("location", "address"), "location"),
    (("linkedin",), "linkedin"),
    (("headline", "current title", "job title"), "headline"),
    (("summary", "cover letter", "additional information", "message", "about"), "summary"),
]

SNAPSHOT_JS = r"""() => {
  document.querySelectorAll('[data-agent-idx]').forEach(e => e.removeAttribute('data-agent-idx'));
  const vis = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const els = [...document.querySelectorAll('a, button, [role="button"]')].filter(vis).slice(0, 80);
  const elements = els.map((el, i) => {
    el.setAttribute('data-agent-idx', i);
    return {
      idx: i,
      tag: el.tagName.toLowerCase(),
      text: ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).trim().replace(/\s+/g, ' ').slice(0, 90),
      href: (el.getAttribute('href') || '').slice(0, 120)
    };
  }).filter(e => e.text || e.href);
  return {
    url: location.href,
    title: document.title,
    bodyPreview: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 500),
    elements
  };
}"""

FORM_COUNT_JS = r"""(selector) => {
  const vis = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  return [...document.querySelectorAll(selector)].filter(vis).length;
}"""

COLLECT_FIELDS_JS = r"""(selector) => {
  const vis = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const out = [];
  document.querySelectorAll(selector).forEach((node) => {
    if (!vis(node)) return;
    const key = 'f' + out.length;
    node.setAttribute('data-fill-key', key);
    const id = node.id || '';
    const labelled = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    const wrap = node.closest('label');
    let label = (labelled && labelled.textContent) || (wrap && wrap.textContent) || '';
    if (!label.trim()) {
      const box = node.closest('.application-question, .card, fieldset, [class*="question"], [class*="field"]')
        || (node.parentElement && node.parentElement.parentElement) || node.parentElement;
      const el = box && (box.querySelector('.application-label, label, .label, strong, b') || box);
      label = el ? (el.innerText || '').split('\n')[0] : '';
    }
    const tag = node.tagName.toLowerCase();
    out.push({
      key,
      tag,
      type: node.getAttribute('type'),
      name: node.getAttribute('name'),
      id,
      placeholder: node.getAttribute('placeholder'),
      aria_label: node.getAttribute('aria-label'),
      autocomplete: node.getAttribute('autocomplete'),
      label: label.replace(/\s+/g, ' ').trim().slice(0, 180),
      options: tag === 'select' ? [...node.options].map(o => o.textContent.trim()).filter(Boolean).slice(0, 40) : null
    });
  });
  return out.slice(0, 80);
}"""


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------
async def _llm_json(prompt: str) -> dict:
    """Call Gemini and parse a JSON object. Needs GEMINI_API_KEY (or GOOGLE_API_KEY) in the env."""
    from google import genai
    from google.genai import types

    client = genai.Client()
    response = await client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
    )
    text = re.sub(r"```(?:json)?", "", response.text or "").strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# LinkedIn login
# ---------------------------------------------------------------------------
async def _is_logged_in(context) -> bool:
    """LinkedIn only sets the `li_at` cookie for authenticated sessions."""
    cookies = await context.cookies("https://www.linkedin.com")
    return any(cookie["name"] == "li_at" for cookie in cookies)


async def _looks_logged_out(page) -> bool:
    """Detect the public job page (sign-in modal on top) or an authwall, even if a stale cookie exists."""
    if "linkedin.com" not in page.url:
        return False
    await page.wait_for_timeout(1500)
    if re.search(r"authwall|/login|/uas/|checkpoint", page.url):
        return True
    markers = '[data-tracking-control-name*="sign-in-modal"], .sign-up-modal__outlet'
    return await page.locator(markers).count() > 0


async def _login_to_linkedin(page, email: str, password: str) -> None:
    logger.info("Attempting automated LinkedIn login...")
    await page.wait_for_timeout(1500)
    if await page.locator("#username").count() > 0:
        await page.fill("#username", email)
        await page.fill("#password", password)
    elif await page.locator("#session_key").count() > 0:
        await page.fill("#session_key", email)
        await page.fill("#session_password", password)
    else:
        raise RuntimeError("Could not locate LinkedIn login fields.")

    await page.click("button[type='submit']")
    try:
        await page.wait_for_url(lambda url: not re.search(r"login|sign-in|checkpoint|authwall", url), timeout=20000)
    except Exception as exc:
        raise RuntimeError(
            "LinkedIn login did not complete (wrong password, CAPTCHA, or a 2FA/security checkpoint). "
            "Log in once manually to clear the checkpoint, then retry."
        ) from exc
    logger.info("Successfully logged in to LinkedIn.")


# ---------------------------------------------------------------------------
# Navigation: LinkedIn job page -> external application form
# ---------------------------------------------------------------------------
async def _find_form_frame(page):
    """Return (frame, visible_input_count) for the frame with the most inputs (handles iframe-embedded ATS forms)."""
    best_frame, best_count = None, 0
    for frame in page.frames:
        try:
            count = await frame.evaluate(FORM_COUNT_JS, FIELD_LOCATOR)
        except Exception:
            continue
        if count > best_count:
            best_frame, best_count = frame, count
    return best_frame, best_count


async def _navigate_to_form(context, page, job: ScrapedJob, on_stage: Callable[[str], Awaitable[None]]):
    history: list[dict] = []

    for step in range(1, MAX_NAV_STEPS + 1):
        frame, count = await _find_form_frame(page)
        if frame is not None and count >= MIN_FORM_FIELDS and "linkedin.com" not in page.url:
            logger.info("Form detected (%s fields) at %s", count, page.url)
            return page, frame

        snap = await page.evaluate(SNAPSHOT_JS)
        # Hard safety rails, independent of the LLM: never offer Easy Apply or Submit buttons.
        elements = [
            e for e in snap["elements"]
            if not EASY_APPLY_PATTERN.search(e["text"]) and not SUBMIT_PATTERN.search(e["text"])
        ]

        prompt = f"""You are a browser agent. Goal: reach the EXTERNAL company job application FORM
(name / email / resume fields) for the job "{job.title}" at "{job.company}". You start from a LinkedIn job posting.

Rules:
- Easy Apply and Submit controls were already removed from the list; never try to work around that.
- Prefer an "Apply" / "Apply on company website" button or link.
- On a company careers page that only shows a job description, click its Apply / Apply Now button.
- Dismiss cookie banners only if they block the Apply button.
- If the only way to apply is LinkedIn Easy Apply, or there is a sign-in wall, CAPTCHA, or no sensible element, answer give_up.

Current page:
URL: {snap["url"]}
Title: {snap["title"]}
Text preview: {snap["bodyPreview"]}

Clickable elements:
{json.dumps(elements)}

Previous actions (do not repeat a failed one): {json.dumps(history)}

Return ONLY raw JSON: {{"action":"click","idx":<number>,"reason":"..."}} or {{"action":"give_up","reason":"..."}}"""

        decision = await _llm_json(prompt)
        logger.info("Navigator step %s: %s", step, decision)

        if decision.get("action") != "click" or not isinstance(decision.get("idx"), int):
            raise RuntimeError(f"Could not reach an external application form: {decision.get('reason', 'no reason given')}")

        await on_stage(f"Navigating to form (step {step}): {str(decision.get('reason', ''))[:70]}")
        idx = decision["idx"]

        # LinkedIn's external Apply usually opens a NEW TAB, so listen for one around the click.
        new_pages: list = []
        handler = new_pages.append
        context.on("page", handler)
        try:
            await page.locator(f'[data-agent-idx="{idx}"]').first.click(timeout=5000)
            await page.wait_for_timeout(2500)
        except Exception as exc:
            history.append({**decision, "error": str(exc).splitlines()[0][:150]})
            continue
        finally:
            context.remove_listener("page", handler)

        history.append(decision)
        if new_pages:
            page = new_pages[-1]
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1500)  # let SPA / iframe forms render

    raise RuntimeError(f"Did not reach an application form within {MAX_NAV_STEPS} steps.")


# ---------------------------------------------------------------------------
# Profile -> field mapping
# ---------------------------------------------------------------------------
def _application_payload(application: Application) -> dict:
    job = application.job
    return {
        "id": application.id,
        "job_id": application.job_id,
        "resume_key": application.resume_key,
        "resume_role": application.resume_role,
        "status": application.status,
        "stage": application.stage,
        "queue_position": application.queue_position,
        "application_url": application.application_url,
        "form_data": application.form_data or {},
        "error": application.error,
        "created_at": application.created_at,
        "updated_at": application.updated_at,
        "started_at": application.started_at,
        "applied_at": application.applied_at,
        "completed_at": application.completed_at,
        "job": {
            "id": job.id,
            "source": job.source,
            "external_url": job.external_url,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "ai_status": job.ai_status,
            "match_score": job.match_score,
        },
    }


def serialize_application(application: Application) -> dict:
    return _application_payload(application)


def choose_resume(username: str, requested_key: str | None) -> tuple[str, str | None]:
    if requested_key:
        if not requested_key.startswith(f"{username.lower()}/Resumes/"):
            raise ValueError("That resume does not belong to you")
        parts = requested_key.split("/")
        return requested_key, (parts[2] if len(parts) >= 4 else None)
    resumes = list_resumes(username)
    if not resumes:
        raise ValueError("Upload a resume before applying to jobs")
    resume = resumes[0]
    return resume["storage_key"], resume.get("role")


def get_browser_session(db: Session, user: User, source: str) -> BrowserSession:
    session = db.query(BrowserSession).filter(BrowserSession.user_id == user.id, BrowserSession.source == source).one_or_none()
    if session is None:
        session = BrowserSession(user_id=user.id, source=source, status="NEEDS_LOGIN")
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


def enqueue_applications(db: Session, user: User, job_ids: list[int], resume_key: str | None) -> list[Application]:
    jobs = db.query(ScrapedJob).filter(ScrapedJob.user_id == user.id, ScrapedJob.id.in_(job_ids)).all()
    jobs_by_id = {job.id: job for job in jobs}
    missing_ids = [job_id for job_id in job_ids if job_id not in jobs_by_id]
    if missing_ids:
        raise ValueError("One or more selected jobs were not found")

    chosen_resume_key, chosen_resume_role = choose_resume(user.username, resume_key)
    max_position = db.query(func.coalesce(func.max(Application.queue_position), 0)).filter(Application.user_id == user.id).scalar() or 0
    applications: list[Application] = []

    for offset, job_id in enumerate(job_ids, start=1):
        job = jobs_by_id[job_id]
        application = db.query(Application).filter(Application.user_id == user.id, Application.job_id == job.id).one_or_none()
        if application is None:
            application = Application(
                user_id=user.id,
                job_id=job.id,
                job=job,
                resume_key=chosen_resume_key,
                resume_role=chosen_resume_role,
                status="PENDING",
                stage="QUEUED",
                queue_position=max_position + offset,
            )
            db.add(application)
        else:
            application.resume_key = chosen_resume_key
            application.resume_role = chosen_resume_role
            application.status = "PENDING"
            application.stage = "QUEUED"
            application.error = None
            application.started_at = None
            application.completed_at = None
            application.applied_at = None
            application.application_url = None
            application.form_data = {}
            application.queue_position = max_position + offset
            application.job = job
        job.application_status = "QUEUED"
        job.application_id = application.id if application.id is not None else None
        job.resume_key = chosen_resume_key
        job.resume_role = chosen_resume_role
        job.applied_at = None
        applications.append(application)

    db.flush()
    for application in applications:
        if application.job is not None:
            application.job.application_id = application.id
    db.commit()
    for application in applications:
        db.refresh(application)
    return applications


async def _publish(db: Session, application: Application, user_id: int, websocket_broker, *, stage: str | None = None, status: str | None = None) -> None:
    if stage is not None:
        application.stage = stage
    if status is not None:
        application.status = status
    db.commit()
    db.refresh(application)
    await websocket_broker.send_to_user(user_id, {"type": "application.updated", "application": _application_payload(application)})


def _field_haystack(field: dict[str, str | None]) -> str:
    return " ".join(filter(None, [field.get("label"), field.get("name"), field.get("id"), field.get("placeholder"), field.get("aria_label"), field.get("autocomplete"), field.get("type")])).lower()


def _profile_answers(username: str) -> dict[str, str]:
    profile = read_profile_markdown(username) or ""
    details = read_details_markdown(username) or ""
    text = f"{profile}\n{details}"
    answers: dict[str, str] = {}
    heading = re.search(r"^#\s+(.+)$", profile, re.M)
    if heading:
        full_name = heading.group(1).strip()
        answers["full_name"] = full_name
        parts = full_name.split()
        answers["first_name"] = parts[0]
        if len(parts) > 1:
            answers["last_name"] = " ".join(parts[1:])
    for label, key in (("Headline", "headline"), ("Location", "location")):
        match = re.search(rf"\*\*{label}:\*\*\s*(.+)", text, re.I)
        if match:
            answers[key] = match.group(1).strip()
    if "location" in answers and "," in answers["location"]:
        city, country = [part.strip() for part in answers["location"].split(",", 1)]
        answers["city"] = city
        answers["country"] = country
    email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    if email:
        answers["email"] = email.group(0)
    phone = re.search(r"(\+?\d[\d\s().-]{8,}\d)", text)
    if phone:
        answers["phone"] = re.sub(r"\s+", " ", phone.group(1)).strip()
    linkedin = re.search(r"https?://(?:www\.)?linkedin\.com/\S+", text, re.I)
    if linkedin:
        answers["linkedin"] = linkedin.group(0).rstrip(").,")
    summary = re.search(r"##\s+Summary\n+(.+?)(?:\n##|\Z)", profile, re.S | re.I)
    if summary:
        answers["summary"] = " ".join(summary.group(1).split())[:2000]
    return {key: value for key, value in answers.items() if value}


def _answer_for_field(field: dict[str, str | None], answers: dict[str, str]) -> str | None:
    haystack = _field_haystack(field)
    if not haystack:
        return None
    for needles, key in FIELD_KEY_PATTERNS:
        if any(needle in haystack for needle in needles) and answers.get(key):
            return answers[key]
    if haystack.strip() in {"name"} and answers.get("full_name"):
        return answers["full_name"]
    return None


async def _collect_input_fields(frame) -> list[dict]:
    """Collect visible fields and tag each in the DOM with data-fill-key so we can target it reliably later."""
    return await frame.evaluate(COLLECT_FIELDS_JS, FIELD_LOCATOR)


async def _resolve_values(fields: list[dict], username: str, answers: dict[str, str]) -> tuple[dict[str, str | None], str]:
    """LLM maps profile -> fields (unknowns come back as ASK_USER and are NOT guessed). Regex is only a fallback if the LLM call fails."""
    profile_text = f"{read_profile_markdown(username) or ''}\n{read_details_markdown(username) or ''}"
    prompt = f"""You are an intelligent form filler.
Applicant profile data:
---
{profile_text}
---

Input fields found on the application page (use "label" to understand what each asks; for selects, "options" lists the allowed choices):
---
{json.dumps(fields, indent=2)}
---

Return ONLY a raw JSON object mapping each field "key" to the string to enter.
CRITICAL: if the profile does NOT explicitly contain the answer (e.g. notice period, CTC), the value MUST be exactly "ASK_USER". Do not guess.
CRITICAL: copy values from the profile exactly, character for character; never shorten or paraphrase.
CRITICAL: for select fields the value must be exactly one of that field's "options", otherwise "ASK_USER".
Example: {{"f0": "John", "f1": "ASK_USER"}}"""
    try:
        mapping = await _llm_json(prompt)
        valid_keys = {field["key"] for field in fields}
        resolved = {
            key: value
            for key, value in mapping.items()
            if key in valid_keys and isinstance(value, str) and value.strip() and value != "ASK_USER"
        }
        return resolved, "llm"
    except Exception as exc:
        logger.warning("LLM field mapping failed, falling back to regex mapping: %s", exc)
        return {field["key"]: _answer_for_field(field, answers) for field in fields}, "regex"


async def _fill_places_autocomplete(page, locator, value: str) -> None:
    """Google Places-style widgets ignore .fill(); they need real keystrokes and a picked suggestion."""
    await locator.click()
    await locator.fill("")
    await locator.press_sequentially(value, delay=80)
    await page.wait_for_timeout(1200)
    await locator.press("ArrowDown")
    await locator.press("Enter")


async def _fill_form_fields(frame, page, fields: list[dict], resolved: dict[str, str | None]) -> dict:
    filled: list[dict] = []
    skipped: list[dict] = []
    for field in fields:
        value = resolved.get(field["key"])
        if not value:
            skipped.append(field)
            continue
        locator = frame.locator(f'[data-fill-key="{field["key"]}"]').first
        try:
            if (field.get("tag") or "").lower() == "select":
                await locator.select_option(label=value, timeout=5000)
            elif field.get("id") == "location-input" or field.get("name") == "location":
                await _fill_places_autocomplete(page, locator, value)
            else:
                await locator.fill(value, timeout=5000)
            filled.append({**field, "filled_with": value})
            logger.info("Filled form field %s (%s)", field["key"], field.get("label"))
        except Exception as exc:
            logger.warning("Could not fill field %s: %s", field, exc)
            skipped.append({**field, "error": str(exc)[:200]})
    needs_input = [
        {"key": f["key"], "label": f.get("label") or f.get("name") or f.get("id"), "name": f.get("name")}
        for f in skipped
        if not resolved.get(f["key"])
    ]
    return {"fields": fields, "filled": filled, "skipped": skipped, "needs_input": needs_input}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
async def process_application(application_id: int, session_factory, user_id: int, websocket_broker=broker) -> None:
    db: Session = session_factory()
    try:
        application = (
            db.query(Application)
            .options(joinedload(Application.job), joinedload(Application.user))
            .filter(Application.id == application_id, Application.user_id == user_id)
            .one_or_none()
        )
        if application is None:
            return
        job = application.job
        user = application.user
        browser_session = get_browser_session(db, user, job.source)
        application.status = "IN_PROGRESS"
        application.started_at = application.started_at or datetime.now(UTC)
        job.application_status = "IN_PROGRESS"
        await _publish(db, application, user_id, websocket_broker, stage="Opening job page")

        async def on_stage(text: str) -> None:
            await _publish(db, application, user_id, websocket_broker, stage=text)

        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError("Playwright is not installed") from exc

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False, args=["--no-sandbox"])
            try:
                context_kwargs = {"viewport": {"width": 1440, "height": 1200}}
                if browser_session.auth_state:
                    context_kwargs["storage_state"] = browser_session.auth_state
                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                await page.goto(job.external_url, wait_until="domcontentloaded", timeout=60000)

                # Login is decided by the real session cookie + logged-out page markers, not by the URL.
                # (A logged-out job page keeps its /jobs/view/ URL and just shows a sign-in modal.)
                if job.source.lower() == "linkedin" and (not await _is_logged_in(context) or await _looks_logged_out(page)):
                    await on_stage("Logging in to LinkedIn")
                    try:
                        email, password = get_linkedin_login(user)
                        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
                        await _login_to_linkedin(page, email, password)
                        if not await _is_logged_in(context):
                            raise RuntimeError("LinkedIn login finished but no session cookie was set.")
                    except Exception as exc:
                        mark_browser_session(db, browser_session, status="NEEDS_LOGIN", error=str(exc)[:500])
                        raise
                    mark_browser_session(db, browser_session, status="READY", auth_state=await context.storage_state())
                    await page.goto(job.external_url, wait_until="domcontentloaded", timeout=60000)
                elif browser_session.auth_state:
                    mark_browser_session(db, browser_session, status="READY")

                await on_stage("Finding application form")
                page, form_frame = await _navigate_to_form(context, page, job, on_stage)

                await on_stage("Fetching input fields")
                fields = await _collect_input_fields(form_frame)
                logger.info("Discovered %s input fields on %s", len(fields), page.url)
                answers = _profile_answers(user.username)
                resolved, mapping_mode = await _resolve_values(fields, user.username, answers)

                await on_stage("Filling form")
                form_data = await _fill_form_fields(form_frame, page, fields, resolved)
                form_data["mapping"] = mapping_mode
                form_data["profile_keys"] = sorted(answers.keys())

                application.form_data = form_data
                application.application_url = page.url
                application.applied_at = datetime.now(UTC)
                application.status = "HUMAN_REVIEW_REQUIRED"
                job.application_status = "HUMAN_REVIEW_REQUIRED"
                job.applied_at = application.applied_at
                job.application_id = application.id
                db.commit()
                db.refresh(application)
                await websocket_broker.send_to_user(user_id, {"type": "application.updated", "application": _application_payload(application)})
            finally:
                await browser.close()
    except Exception as exc:
        logger.exception("Application %s failed", application_id)
        try:
            application = db.query(Application).filter(Application.id == application_id, Application.user_id == user_id).one_or_none()
            if application is not None:
                application.status = "FAILED"
                application.stage = "failed"
                application.error = str(exc)[:2000]
                if application.job is not None:
                    application.job.application_status = "FAILED"
                db.commit()
                db.refresh(application)
                await websocket_broker.send_to_user(user_id, {"type": "application.updated", "application": _application_payload(application)})
        finally:
            pass
    finally:
        db.close()