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
import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.db.models import Application, ScrapedJob, User
from app.services.agent_assist import questions
from app.services.agent_log import AgentLog
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
from app.services.storage import list_resumes
from app.ws.broker import broker

logger = logging.getLogger(__name__)

# Stage labels — LiveWorkflow.tsx maps these (by substring) onto its flow nodes.
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
        if not requested_key.startswith(f"{username.lower()}/Resumes/"):
            raise ValueError("That resume does not belong to you")
        parts = requested_key.split("/")
        return requested_key, (parts[2] if len(parts) >= 4 else None)
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
