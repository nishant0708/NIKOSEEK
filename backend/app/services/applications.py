"""Application queue + the live apply pipeline.

Pipeline per application (all inside the user's persistent LinkedIn window):
    1. Opening job page        -> new tab in the user's LinkedIn browser
    2. Checking LinkedIn login -> if the session expired, the same window shows /login and waits for the user
    3. Finding apply button    -> LinkedIn Easy Apply dialog, or LLM-guided clicks to the company form (job_navigator);
                                  the AI checks every candidate tab really belongs to this job
    4. Filling the form        -> FormSession: AI fills empty fields (+ resume); Easy Apply auto-advances steps
    5. Your review             -> the tab stays OPEN; missing answers / dropdowns are filled from the Live Workflow
                                  page (POST /applications/{id}/fields), then the user presses Submit there

Every step writes to job_application_events (agent_log.py) and is streamed to the Live Workflow page.

Helpers live in:
    linkedin_browser.py  persistent browser per user, login detection, interactive login
    job_navigator.py     LinkedIn posting -> company application form
    agent_log.py         persisted + live-streamed AI reasoning / actions
    form_filler.py       field discovery (text/select/radio/checkbox/combobox), profile mapping, filling
    form_session.py      the still-open form the user finishes + submits from the UI
    prompts.py           editable prompts (Config workflow page)
"""
from __future__ import annotations

import asyncio
import html
import logging
import smtplib
from email.message import EmailMessage
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.db.models import Application, ScrapedJob, User
from app.core.config import settings
from app.services.agent_assist import questions
from app.services.agent_log import AgentLog
from app.services.llm import LLMUnavailableError
from app.services.live_view import live_view
from app.services.form_filler import remember_answers, remember_notes
from app.services.form_session import FormSession, form_sessions
from app.services.job_navigator import navigate_to_form, normalize_job_url
from app.services.linkedin_browser import (
    READY,
    SOURCE as LINKEDIN,
    LinkedInAuthRequired,
    get_browser_session,
    linkedin_browser,
)
from app.services.indeed_browser import (
    SOURCE as INDEED,
    IndeedAuthRequired,
    indeed_browser,
)
from app.services.resume_agent import load_resume_rows, pin_resume, resume_agent_settings, run_resume_agent
from app.services.storage import list_resumes, resume_belongs_to, resume_role_of
from app.ws.broker import broker

logger = logging.getLogger(__name__)

# Stage labels — LiveWorkflow.tsx maps these (by substring) onto its flow nodes.
STAGE_RESUME = "Resume agent — choosing the best resume for this job"
STAGE_OPENING = "Opening job page"
STAGE_LOGIN_CHECK = "Checking {provider} login"
STAGE_LOGIN_WAIT = "Waiting for {provider} login in the browser window"
STAGE_QUESTION = "Waiting for your answer — {question}"
STAGE_FINDING = "Finding apply button"
STAGE_FIELDS = "Filling the form"
STAGE_NEEDS_INPUT = "Needs your input ({n} field(s))"
STAGE_READY = "Ready to submit — review and press Submit"
STAGE_SUBMITTED = "Submitted"
SUBMITTED = "SUBMITTED"


async def _send_application_receipt(application: Application, snap: dict) -> None:
    """Email the user a copy of the values that were present after a successful submit."""
    config = application.user.agent_config or {}
    recipients = [entry.get("email") for entry in (config.get("linkedin"), config.get("indeed"))
                  if isinstance(entry, dict) and isinstance(entry.get("email"), str) and entry["email"].strip()]
    recipient = settings.smtp_receipt_email or next(iter(dict.fromkeys(recipients)), None)
    if not recipient:
        logger.info("No recipient email configured for application %s; receipt email skipped", application.id)
        return
    if not settings.smtp_host or not settings.smtp_from_email:
        logger.warning("SMTP is not configured; application receipt email skipped for application %s", application.id)
        return
    answers = [a for a in snap.get("answers", []) if a.get("value")]
    lines = [f"{a.get('label', 'Field')}: {a.get('value', '')}" for a in answers]
    title = application.job.title or "Job application"
    company = application.job.company or "Company not specified"
    url = application.application_url or ""
    message = EmailMessage()
    message["Subject"] = f"Application submitted — {title} at {company}"
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message.set_content(
        "APPLICATION SUBMITTED\n\n"
        "Your application was submitted successfully. Here is a copy of the information recorded on the form.\n\n"
        f"Position: {title}\nCompany: {company}\n"
        + (f"Application page: {url}\n" if url else "")
        + "\nINFORMATION SUBMITTED\n"
        + ("\n".join(lines) if lines else "No form answers were captured.")
        + "\n\nThis is an automated confirmation for your records."
    )
    answer_rows = "".join(
        "<tr>"
        f"<td style=\"padding:12px 14px;border-bottom:1px solid #eee7df;color:#6b5142;font-weight:600;width:38%;vertical-align:top\">{html.escape(str(answer.get('label') or 'Field'))}</td>"
        f"<td style=\"padding:12px 14px;border-bottom:1px solid #eee7df;color:#29231f;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere\">{html.escape(str(answer.get('value') or ''))}</td>"
        "</tr>"
        for answer in answers
    )
    application_link = (
        f'<p style="margin:22px 0 0"><a href="{html.escape(url, quote=True)}" '
        'style="display:inline-block;background:#e86e32;color:#fff;text-decoration:none;font-weight:700;'
        'padding:11px 18px;border-radius:8px">View application page</a></p>'
        if url else ""
    )
    html_body = f"""<!doctype html>
<html lang="en"><body style="margin:0;padding:0;background:#f5f2ee;font-family:Arial,Helvetica,sans-serif;color:#29231f">
  <div style="max-width:640px;margin:32px auto;padding:0 16px">
    <div style="background:#30251f;padding:20px 28px;border-radius:14px 14px 0 0;color:#fff">
      <div style="font-size:12px;letter-spacing:1.6px;text-transform:uppercase;color:#f4b28c;font-weight:700">Application update</div>
      <div style="font-size:22px;line-height:1.3;font-weight:700;margin-top:8px">Your application was submitted</div>
    </div>
    <div style="background:#fff;padding:28px;border:1px solid #e9e1d9;border-top:0;border-radius:0 0 14px 14px">
      <p style="margin:0 0 20px;line-height:1.6;color:#59483d">The application was submitted successfully. Keep this email as a record of the information entered.</p>
      <div style="background:#fbf8f4;border:1px solid #eee7df;border-radius:10px;padding:16px 18px">
        <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#98745e;font-weight:700">Position</div>
        <div style="font-size:17px;font-weight:700;margin-top:5px">{html.escape(str(title))}</div>
        <div style="font-size:14px;color:#6b5142;margin-top:4px">{html.escape(str(company))}</div>
      </div>
      <h2 style="font-size:15px;margin:26px 0 10px">Information submitted</h2>
      {('<div style="border:1px solid #eee7df;border-radius:10px;overflow:hidden"><table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;font-size:13px">' + answer_rows + '</table></div>') if answer_rows else '<p style="color:#6b5142;font-size:14px">No form answers were captured.</p>'}
      {application_link}
      <p style="border-top:1px solid #eee7df;margin:28px 0 0;padding-top:16px;color:#9a8b80;font-size:11px;line-height:1.5">This is an automated confirmation for your records. Please contact the employer directly if you need to update your application.</p>
    </div>
  </div>
</body></html>"""
    message.add_alternative(html_body, subtype="html")

    def deliver() -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or "")
            server.send_message(message)
    try:
        await asyncio.to_thread(deliver)
        logger.info("Sent application receipt email for application %s", application.id)
    except Exception:
        logger.exception("Could not send application receipt email for application %s", application.id)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def _live_form_data(application: Application) -> dict:
    data = dict(application.form_data or {})
    if data.get("fields") is not None:
        data["session_open"] = form_sessions.get(application.id) is not None
    return data


def serialize_application(application: Application) -> dict:
    job = application.job
    return {
        "id": application.id,
        "job_id": application.job_id,
        "resume_key": application.resume_key,
        "resume_role": application.resume_role,
        "resume_agent": dict(application.resume_agent or {}),
        "status": application.status,
        "stage": application.stage,
        "queue_position": application.queue_position,
        "application_url": application.application_url,
        "form_data": _live_form_data(application),
        "error": application.error,
        "created_at": application.created_at,
        "updated_at": application.updated_at,
        "started_at": application.started_at,
        "applied_at": application.applied_at,
        "completed_at": application.completed_at,
        "llm_input_tokens": application.llm_input_tokens or 0,
        "llm_output_tokens": application.llm_output_tokens or 0,
        "llm_total_tokens": application.llm_total_tokens or 0,
        "llm_calls": application.llm_calls or 0,
        "pending_question": questions.pending(application.id),
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


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------
def choose_resume(username: str, requested_key: str | None) -> tuple[str, str | None]:
    if requested_key:
        if not resume_belongs_to(username, requested_key):
            raise ValueError("That resume does not belong to you")
        return requested_key, resume_role_of(requested_key)
    resumes = list_resumes(username)
    if not resumes:
        raise ValueError("Upload a resume before applying to jobs")
    resume = resumes[0]
    return resume["storage_key"], resume.get("role")


def linkedin_connected(db: Session, user: User) -> bool:
    return get_browser_session(db, user, LINKEDIN).status == READY


def application_connection_error(db: Session, user: User, job_ids: list[int]) -> str | None:
    """Return the provider-specific login requirement for the selected jobs."""
    sources = {
        source.lower()
        for (source,) in db.query(ScrapedJob.source)
        .filter(ScrapedJob.user_id == user.id, ScrapedJob.id.in_(job_ids)).all()
    }
    if LINKEDIN in sources and not linkedin_connected(db, user):
        return "LINKEDIN_NOT_CONNECTED"
    if INDEED in sources and get_browser_session(db, user, INDEED).status != READY:
        return "INDEED_NOT_CONNECTED"
    return None


def enqueue_applications(db: Session, user: User, job_ids: list[int], resume_key: str | None) -> list[Application]:
    jobs = db.query(ScrapedJob).filter(ScrapedJob.user_id == user.id, ScrapedJob.id.in_(job_ids)).all()
    jobs_by_id = {job.id: job for job in jobs}
    if any(job_id not in jobs_by_id for job_id in job_ids):
        raise ValueError("One or more selected jobs were not found")

    chosen_resume_key, chosen_resume_role = choose_resume(user.username, resume_key)
    max_position = db.query(func.coalesce(func.max(Application.queue_position), 0)).filter(Application.user_id == user.id).scalar() or 0
    applications: list[Application] = []

    for offset, job_id in enumerate(job_ids, start=1):
        job = jobs_by_id[job_id]
        application = db.query(Application).filter(Application.user_id == user.id, Application.job_id == job.id).one_or_none()
        if application is None:
            application = Application(user_id=user.id, job_id=job.id, job=job)
            db.add(application)
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
        # A resume you chose (Retry / API) is pinned; otherwise the resume agent (when ON) decides at run time.
        application.resume_agent = {}
        if resume_key:
            pin_resume(application, chosen_resume_key, None)
        application.queue_position = max_position + offset
        application.job = job
        job.application_status = "QUEUED"
        job.resume_key = chosen_resume_key
        job.resume_role = chosen_resume_role
        job.applied_at = None
        applications.append(application)

    db.flush()
    for application in applications:
        application.job.application_id = application.id
    db.commit()
    for application in applications:
        db.refresh(application)
    return applications


# ---------------------------------------------------------------------------
# Live pipeline
# ---------------------------------------------------------------------------
class _Run:
    """Owns the DB row, websocket updates and the agent log for one application run."""

    def __init__(self, db: Session, application: Application, websocket_broker, *, continue_attempt: bool = False) -> None:
        self.db = db
        self.application = application
        self.broker = websocket_broker
        self.log = AgentLog(db, application, websocket_broker, continue_attempt=continue_attempt, stage_key="review" if continue_attempt else "open")
        self._stage_before_question: str | None = None
        self.log.on_question = self._on_question
        self.log.on_question_closed = self._on_question_closed

    async def _on_question(self, entry: dict) -> None:
        self._stage_before_question = self.application.stage
        await self.publish(stage=STAGE_QUESTION.format(question=entry["question"])[:100])

    async def _on_question_closed(self) -> None:
        await self.publish(stage=self._stage_before_question or STAGE_FINDING)

    async def publish(self, *, stage: str | None = None, status: str | None = None) -> None:
        if stage is not None:
            self.application.stage = stage[:100]
        if status is not None:
            self.application.status = status
            self.application.job.application_status = status
        self.db.commit()
        self.db.refresh(self.application)
        await self.broker.send_to_user(self.application.user_id, {"type": "application.updated", "application": serialize_application(self.application)})

    async def stage(self, stage_key: str, text: str, *, status: str | None = None) -> None:
        """Move to a new flow node: updates the row, streams it and records a 'stage' event."""
        self.log.set_stage(stage_key)
        await self.publish(stage=text, status=status)
        await self.log("stage", text)


def _review_stage(snap: dict) -> str:
    if snap["missing_required"]:
        return STAGE_NEEDS_INPUT.format(n=len(snap["missing_required"]))
    if snap["can_submit"]:
        return STAGE_READY
    if snap["has_next"]:
        return "Review this step and press Continue"
    return "Review the form in the browser window"


async def _run_pipeline(run: _Run, user: User) -> None:
    application, job, db, log = run.application, run.application.job, run.db, run.log
    source = job.source.lower()
    if source == INDEED:
        browser, provider = indeed_browser, "Indeed"
    else:
        browser, provider = linkedin_browser, "LinkedIn"
    browser_session = get_browser_session(db, user, source)
    job_url = normalize_job_url(job.external_url)
    await form_sessions.close(application.id)  # a retry replaces any old open form

    # 1. Open the posting in a new tab of the selected provider's window.
    await run.stage("open", STAGE_OPENING, status="IN_PROGRESS")
    context = await browser.get_context(user.id, browser_session.auth_state)
    page = await context.new_page()  # always a fresh tab owned by THIS application
    await page.bring_to_front()
    await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
    log.track(page)
    await log("action", f"Opened {page.url}", data={"url": page.url, "job_url": job_url})

    # 2. Make sure the selected provider session is active in this same window.
    async def relogin(target_page) -> None:
        async def on_wait() -> None:
            await run.publish(stage=STAGE_LOGIN_WAIT.format(provider=provider))
            await log("warning", f"{provider} is not signed in. Finish logging in in the browser window (2FA / verify-it's-you included).", stage_key="login")
        await browser.ensure_logged_in(db, user, target_page, on_wait=on_wait)
        await log("info", f"{provider} session is active.", stage_key="login")

    await run.stage("login", STAGE_LOGIN_CHECK.format(provider=provider))
    await relogin(page)
    if source == LINKEDIN and not page.is_closed() and "/jobs/view/" not in page.url:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=60000)

    # 2b. Resume agent (Workflow page toggle): score your resumes against this JD, tailor one if it's worth it.
    #     Runs on the open posting so a missing JD (older LinkedIn searches) can be read from the page.
    if resume_agent_settings(user)["enabled"]:
        await run.stage("resume", STAGE_RESUME)
        try:
            await run_resume_agent(db, user, application, log, page=page)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Resume agent failed for application %s", application.id)
            db.rollback()
            await log("warning", f"Resume agent failed ({str(exc).splitlines()[0][:160]}) — continuing with {application.resume_role or 'your resume'}.")
        await run.publish()

    # 3. Reach the form: Easy Apply dialog or the company's site (AI-guided, wrong-tab guarded).
    await run.stage("find", STAGE_FINDING)
    job_page = page
    page, form_frame, mode = await navigate_to_form(context, page, job, user, log, relogin, exclude=form_sessions.pages())
    log.set_stage("find")
    if page is not job_page and not job_page.is_closed():
        await job_page.close()  # keep only the form tab around for review

    # 4. Fill everything the profile can answer (Easy Apply: step by step).
    await run.stage("fields", STAGE_FIELDS)
    session = FormSession(application_id=application.id, user_id=user.id, page=page, mode=mode, resume_key=application.resume_key, frame=form_frame)
    form_sessions.put(session)
    async with session.lock:
        snap = await session.autofill(user, job, log)

    # 5. Hand over: the user completes missing answers and presses Submit in the Live Workflow page.
    application.form_data = snap
    application.application_url = page.url
    job.application_id = application.id
    await page.bring_to_front()
    if bool((user.agent_config or {}).get("auto_submit")):
        async with session.lock:
            # Advance only through complete steps; never skip an unanswered required field.
            for _ in range(12):
                if not snap.get("has_next") or snap.get("missing_required"):
                    break
                before_step = snap.get("step")
                snap = await session.next_step(user, job, log)
                if snap.get("step") == before_step:
                    break
            if snap.get("can_submit") and not snap.get("missing_required"):
                ok, snap, message = await session.submit(log)
                application.form_data = snap
                if ok:
                    now = datetime.now(UTC)
                    application.completed_at = now
                    application.applied_at = now
                    job.applied_at = now
                    snap["session_open"] = False
                    await run.stage("review", "Submitted automatically", status=SUBMITTED)
                    await form_sessions.close(application.id)
                    await _send_application_receipt(application, snap)
                    return
                await log("warning", f"Automatic submission stopped: {message}")
    await run.stage("review", _review_stage(snap), status="HUMAN_REVIEW_REQUIRED")


# application_id -> the asyncio task running it (so the user can stop a stuck run).
running_tasks: dict[int, asyncio.Task] = {}


async def process_application(application_id: int, session_factory, user_id: int, websocket_broker=broker) -> None:
    db: Session = session_factory()
    try:
        application = (
            db.query(Application)
            .options(joinedload(Application.job), joinedload(Application.user))
            .filter(Application.id == application_id, Application.user_id == user_id)
            .one_or_none()
        )
        if application is None or application.status != "PENDING":
            return  # removed / moved by the user, or a duplicate queue entry
        application.started_at = application.started_at or datetime.now(UTC)
        run = _Run(db, application, websocket_broker)
        task = asyncio.current_task()
        if task is not None:
            running_tasks[application_id] = task
        try:
            # One run at a time per user browser (also blocks while the Connect popup is open).
            browser = indeed_browser if application.job.source.lower() == INDEED else linkedin_browser
            async with browser.lock_for(user_id):
                await _run_pipeline(run, application.user)
        except asyncio.CancelledError:
            # Stopped from the UI ("Move to failed"). The status was already set by set_application_status.
            logger.info("Application %s run was stopped by the user", application_id)
            db.rollback()
        except LLMUnavailableError as exc:
            logger.warning("Application %s paused because all configured AI models were unavailable", application_id)
            db.rollback()
            application.error = str(exc)[:2000]
            await run.publish(stage="AI unavailable — live application session retained for review", status="HUMAN_REVIEW_REQUIRED")
            await run.log("warning", "AI models were unavailable. The application was left open for review and was not marked failed.", data={"type": type(exc).__name__})
        except Exception as exc:
            if isinstance(exc, (LinkedInAuthRequired, IndeedAuthRequired)):
                logger.warning("Application %s needs provider login: %s", application_id, exc)
            else:
                logger.exception("Application %s failed", application_id)
            db.rollback()
            application.error = str(exc)[:2000]
            await run.publish(stage="failed", status="FAILED")
            await run.log("error", str(exc)[:1000], data={"type": type(exc).__name__, "failed_at": run.log.stage_key})
        finally:
            running_tasks.pop(application_id, None)
    finally:
        db.close()


MANUAL_STATUS_STAGE = {"FAILED": "Moved to failed by you", "COMPLETED": "Marked completed by you", "REJECTED": "Marked rejected by you"}


async def set_application_status(db: Session, user: User, application_id: int, status: str, reason: str | None, websocket_broker=broker) -> dict:
    """Manual override from the queue: stop a stuck run, or close out a review item as completed / rejected."""
    application = (
        db.query(Application).options(joinedload(Application.job))
        .filter(Application.id == application_id, Application.user_id == user.id).one_or_none()
    )
    if application is None:
        raise FormActionError("Application not found", 404)
    task = running_tasks.get(application_id)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.sleep(0.2)
    db.refresh(application)
    await form_sessions.close(application_id)
    if status == "FAILED":
        application.error = reason or "Moved to failed by you."
    if status in ("COMPLETED", "REJECTED"):
        application.completed_at = datetime.now(UTC)
        if status == "COMPLETED":
            application.applied_at = application.applied_at or application.completed_at
            application.job.applied_at = application.applied_at
    data = dict(application.form_data or {})
    if data.get("fields") is not None:
        data["session_open"] = False
        application.form_data = data
    run = _Run(db, application, websocket_broker, continue_attempt=True)
    await run.publish(stage=MANUAL_STATUS_STAGE[status], status=status)
    await run.log("info", MANUAL_STATUS_STAGE[status] + (f": {reason}" if reason else "."), data={"status": status})
    return serialize_application(application)


# ---------------------------------------------------------------------------
# User actions on an open form (Live Workflow page)
# ---------------------------------------------------------------------------
class FormActionError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _load(db: Session, user: User, application_id: int) -> tuple[Application, FormSession]:
    application = (
        db.query(Application).options(joinedload(Application.job))
        .filter(Application.id == application_id, Application.user_id == user.id).one_or_none()
    )
    if application is None:
        raise FormActionError("Application not found", 404)
    if application.status == SUBMITTED:
        raise FormActionError("This application was already submitted.")
    session = form_sessions.get(application_id)
    if session is None:
        raise FormActionError("The form is no longer open in the browser (tab closed or backend restarted). Press Retry to run it again.", 410)
    return application, session


async def _finish_action(db: Session, application: Application, run: _Run, snap: dict, *, status: str = "HUMAN_REVIEW_REQUIRED", stage: str | None = None) -> dict:
    application.form_data = snap
    await run.stage("review", stage or _review_stage(snap), status=status)
    return serialize_application(application)


async def fill_from_user(db: Session, user: User, application_id: int, values: dict[str, str], remember: bool, websocket_broker=broker) -> dict:
    application, session = _load(db, user, application_id)
    run = _Run(db, application, websocket_broker, continue_attempt=True)
    async with session.lock:
        snap = await session.apply_values(values, run.log)
        if remember:
            labels = {f["key"]: f["label"] for f in snap["fields"]}
            answers = [(labels.get(key, key), value) for key, value in values.items() if value and key in session.user_keys]
            try:
                await asyncio.to_thread(remember_answers, user.username, answers)
                await run.log("info", f"Saved {len(answers)} answer(s) to your details for next time.")
            except Exception as exc:
                await run.log("warning", f"Could not save answers to your details: {exc}")
        # Easy Apply: once this step is complete, keep going automatically.
        if session.stepped and not snap["missing_required"] and snap["has_next"]:
            snap = await session.next_step(user, application.job, run.log)
    return await _finish_action(db, application, run, snap)


async def continue_form(db: Session, user: User, application_id: int, websocket_broker=broker) -> dict:
    application, session = _load(db, user, application_id)
    run = _Run(db, application, websocket_broker, continue_attempt=True)
    async with session.lock:
        snap = await session.next_step(user, application.job, run.log) if session.stepped else await session.autofill(user, application.job, run.log)
    return await _finish_action(db, application, run, snap)


async def submit_form(db: Session, user: User, application_id: int, websocket_broker=broker) -> dict:
    application, session = _load(db, user, application_id)
    run = _Run(db, application, websocket_broker, continue_attempt=True)
    async with session.lock:
        ok, snap, message = await session.submit(run.log)
    if not ok:
        await run.log("warning", message)
        application.form_data = snap
        await run.publish(stage=message[:100])
        raise FormActionError(message, 422)
    await run.log("info", message)
    application.completed_at = datetime.now(UTC)
    application.applied_at = application.completed_at
    application.job.applied_at = application.applied_at
    snap["session_open"] = False
    result = await _finish_action(db, application, run, snap, status=SUBMITTED, stage=STAGE_SUBMITTED)
    await form_sessions.close(application_id)
    return result


async def answer_question(db: Session, user: User, application_id: int, question_id: str, answer: dict) -> dict:
    """Your reply to a question the agent asked while it was stuck (option button / instruction / done)."""
    application = db.query(Application).options(joinedload(Application.job)).filter(
        Application.id == application_id, Application.user_id == user.id).one_or_none()
    if application is None:
        raise FormActionError("Application not found", 404)
    if not questions.answer(application_id, question_id, answer):
        raise FormActionError("That question is no longer open — the agent may have moved on.", 409)
    return serialize_application(application)


async def instruct_form(db: Session, user: User, application_id: int, instruction: str, websocket_broker=broker) -> dict:
    """Free-text instruction on an open form ("click Continue", "choose India as country")."""
    application, session = _load(db, user, application_id)
    run = _Run(db, application, websocket_broker, continue_attempt=True)
    async with session.lock:
        snap = await session.instruct(instruction, user, application.job, run.log)
        try:
            await asyncio.to_thread(remember_notes, user.username, [instruction])
            await run.log("info", "Saved your instruction to your profile details, so future applications reuse it.")
        except Exception as exc:
            logger.warning("Could not save instruction notes: %s", exc)
        if session.stepped and snap["has_next"]:
            if not snap["missing_required"]:
                snap = await session.autofill(user, application.job, run.log, snap)
            elif all(f["value"] for f in snap["fields"]):
                # Everything has an answer now (maybe in the wrong format): try Next — it auto-corrects rejected values once.
                snap = await session.next_step(user, application.job, run.log)
    return await _finish_action(db, application, run, snap)


async def recheck_form(db: Session, user: User, application_id: int, websocket_broker=broker) -> dict:
    """“Check with my profile”: re-validate all fields on the open step and fix mismatches (then keep going if complete)."""
    application, session = _load(db, user, application_id)
    run = _Run(db, application, websocket_broker, continue_attempt=True)
    async with session.lock:
        snap = await session.recheck(user, application.job, run.log)
        if session.stepped and not snap["missing_required"] and snap["has_next"]:
            snap = await session.autofill(user, application.job, run.log, snap)
    return await _finish_action(db, application, run, snap)


async def switch_resume(db: Session, user: User, application_id: int, resume_key: str, websocket_broker=broker) -> tuple[dict, str]:
    """Use a different resume for one application (Live Workflow → Resume tile). Pins it so the agent won't override it."""
    application = (
        db.query(Application).options(joinedload(Application.job))
        .filter(Application.id == application_id, Application.user_id == user.id).one_or_none()
    )
    if application is None:
        raise FormActionError("Application not found", 404)
    if application.status in (SUBMITTED, "COMPLETED", "REJECTED"):
        raise FormActionError("This application is already closed.")
    if not resume_belongs_to(user.username, resume_key):
        raise FormActionError("That resume does not belong to you", 403)
    rows = await asyncio.to_thread(load_resume_rows, user.id)
    row = next((r for r in rows if r.storage_key == resume_key), None)
    if row is None:
        raise FormActionError("Resume not found", 404)
    application.resume_key = row.storage_key
    application.resume_role = row.category
    application.job.resume_key = row.storage_key
    application.job.resume_role = row.category
    pin_resume(application, row.storage_key, row.label)
    run = _Run(db, application, websocket_broker, continue_attempt=True)
    session = form_sessions.get(application_id)
    if session is None or not session.is_open:
        later = application.status in ("PENDING", "IN_PROGRESS")
        await run.log("info", f"Resume switched to {row.label}." + (" Used when the form opens." if later else " Press Retry to apply with it."))
        await run.publish()
        return serialize_application(application), ("queued" if later else "retry")
    async with session.lock:
        snap, attached = await session.switch_resume(row.storage_key, user, run.log)
    if not attached:
        await run.log("warning", f"Resume switched to {row.label}, but this step has no resume field. "
                      "Go back to the resume step (instruct the agent: “go back to the resume step”) or press Retry.")
    application.form_data = snap
    await run.publish()
    return serialize_application(application), ("attached" if attached else "not_on_this_step")

