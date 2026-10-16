from __future__ import annotations

import asyncio
import json

import jwt
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.db.models import Application, User
from app.schemas.applications import ApplicationAnswer, ApplicationFieldValues, ApplicationInstruction, ApplicationResumeSwitch, ApplicationStatusUpdate, BrowserInput, ApplicationQueueRequest, ApplicationQueueResponse, ApplicationRead
from app.services.agent_log import list_events, serialize_event
from app.services.applications import (
    FormActionError, answer_question, application_connection_error, continue_form, enqueue_applications, fill_from_user, instruct_form, recheck_form,
    serialize_application, set_application_status, submit_form, switch_resume,
)
from app.services.live_view import live_view
from app.db.session import SessionLocal
from app.ws.broker import broker

router = APIRouter(prefix="/applications", tags=["applications"])


def _resolve_user_from_token(db: DbSession, token: str) -> User:
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token")
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
        username = payload.get("sub")
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    if not isinstance(username, str):
        raise unauthorized
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise unauthorized
    return user


def _serialize(application: Application) -> ApplicationRead:
    return ApplicationRead.model_validate(serialize_application(application))


@router.get("")
def list_applications(user: CurrentUser, db: DbSession) -> list[ApplicationRead]:
    applications = (
        db.query(Application)
        .options(joinedload(Application.job))
        .filter(Application.user_id == user.id)
        .order_by(Application.queue_position.asc(), Application.created_at.asc())
        .all()
    )
    return [_serialize(application) for application in applications]


@router.get("/{application_id}/events")
def application_events(application_id: int, user: CurrentUser, db: DbSession, attempt: int | None = None) -> dict:
    """Agent log (stages, AI reasoning, actions, errors) for one run; latest attempt by default."""
    application = db.query(Application).filter(Application.id == application_id, Application.user_id == user.id).one_or_none()
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    chosen, events = list_events(db, application_id, attempt)
    return {"application_id": application_id, "attempt": chosen, "events": [serialize_event(event) for event in events]}


async def _form_action(coro) -> ApplicationRead:
    try:
        return ApplicationRead.model_validate(await coro)
    except FormActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/{application_id}/fields", response_model=ApplicationRead)
async def fill_application_fields(application_id: int, payload: ApplicationFieldValues, user: CurrentUser, db: DbSession) -> ApplicationRead:
    """Fill answers the user typed / picked in the Live Workflow page into the open form."""
    return await _form_action(fill_from_user(db, user, application_id, payload.values, payload.remember))


@router.post("/{application_id}/continue", response_model=ApplicationRead)
async def continue_application(application_id: int, user: CurrentUser, db: DbSession) -> ApplicationRead:
    """Easy Apply: go to the next step (and autofill it). External forms: re-run the AI fill."""
    return await _form_action(continue_form(db, user, application_id))


@router.post("/{application_id}/submit", response_model=ApplicationRead)
async def submit_application(application_id: int, user: CurrentUser, db: DbSession) -> ApplicationRead:
    """Press the form's Submit button. Only ever triggered by the user."""
    return await _form_action(submit_form(db, user, application_id))


@router.post("/{application_id}/status", response_model=ApplicationRead)
async def update_application_status(application_id: int, payload: ApplicationStatusUpdate, user: CurrentUser, db: DbSession) -> ApplicationRead:
    """Manual override: FAILED (stops a stuck run), COMPLETED or REJECTED."""
    return await _form_action(set_application_status(db, user, application_id, payload.status, payload.reason))


@router.post("/{application_id}/answer", response_model=ApplicationRead)
async def answer_application_question(application_id: int, payload: ApplicationAnswer, user: CurrentUser, db: DbSession) -> ApplicationRead:
    """Reply to the agent's question: pick an option, give an instruction, or say you've done it in the browser."""
    answer = {"option": payload.option} if payload.option is not None else {"instruction": payload.instruction.strip()} if payload.instruction and payload.instruction.strip() else {"done": True}
    return await _form_action(answer_question(db, user, application_id, payload.question_id, answer))


@router.post("/{application_id}/recheck", response_model=ApplicationRead)
async def recheck_application(application_id: int, user: CurrentUser, db: DbSession) -> ApplicationRead:
    """Validate every field on the open step against your profile and fix what doesn't match."""
    return await _form_action(recheck_form(db, user, application_id))


@router.post("/{application_id}/instruct", response_model=ApplicationRead)
async def instruct_application(application_id: int, payload: ApplicationInstruction, user: CurrentUser, db: DbSession) -> ApplicationRead:
    """Tell the agent what to do on the open form, in your own words."""
    return await _form_action(instruct_form(db, user, application_id, payload.instruction.strip()))


@router.post("/{application_id}/resume")
async def switch_application_resume(application_id: int, payload: ApplicationResumeSwitch, user: CurrentUser, db: DbSession) -> dict:
    """Use another of your resumes for this job (pinned: the resume agent won't override it).
    result: attached (re-attached on the open form) | not_on_this_step | queued | retry."""
    try:
        application, result = await switch_resume(db, user, application_id, payload.resume_key)
    except FormActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"application": ApplicationRead.model_validate(application).model_dump(mode="json"), "result": result}


@router.post("/{application_id}/browser/input")
async def browser_input(application_id: int, payload: BrowserInput, user: CurrentUser, db: DbSession) -> dict:
    """Forward a click / key / scroll from the live browser view to the agent's tab."""
    owned = db.query(Application.id).filter(Application.id == application_id, Application.user_id == user.id).first()
    if owned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    try:
        url = await live_view.handle_input(application_id, payload.model_dump(exclude_none=True))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc).splitlines()[0][:200]) from exc
    return {"url": url}


@router.post("/apply", response_model=ApplicationQueueResponse)
async def apply_jobs(payload: ApplicationQueueRequest, user: CurrentUser, db: DbSession, request: Request) -> ApplicationQueueResponse:
    connection_error = application_connection_error(db, user, payload.job_ids)
    if connection_error:
        raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail=connection_error)
    try:
        applications = enqueue_applications(db, user, payload.job_ids, payload.resume_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    queue: asyncio.Queue[tuple[int, int, float]] = request.app.state.application_queue
    for application in applications:
        queue.put_nowait((user.id, application.id, 0.0))
    return ApplicationQueueResponse(message="Applications queued", applications=[_serialize(application) for application in applications])


@router.websocket("/ws")
async def application_stream(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return
    db = SessionLocal()
    try:
        user = _resolve_user_from_token(db, token)
    except HTTPException:
        db.close()
        await websocket.close(code=4401)
        return
    await broker.connect(user.id, websocket)
    watch_task: asyncio.Task | None = None

    def stop_watch() -> None:
        nonlocal watch_task
        if watch_task is not None and not watch_task.done():
            watch_task.cancel()
        watch_task = None

    try:
        snapshot = [application.model_dump(mode="json") for application in list_applications(user, db)]
        await websocket.send_json({"type": "snapshot", "applications": snapshot})
        while True:
            text = await websocket.receive_text()
            try:
                message = json.loads(text)
            except ValueError:
                continue  # keep-alive "ping"
            if not isinstance(message, dict):
                continue
            # Live browser view: {"type":"browser.watch","application_id":N} / {"type":"browser.unwatch"}
            if message.get("type") == "browser.watch" and isinstance(message.get("application_id"), int):
                owned = db.query(Application.id).filter(Application.id == message["application_id"], Application.user_id == user.id).first()
                stop_watch()
                if owned:
                    watch_task = asyncio.create_task(live_view.stream(websocket, message["application_id"]))
            elif message.get("type") == "browser.unwatch":
                stop_watch()
    except WebSocketDisconnect:
        broker.disconnect(user.id, websocket)
    finally:
        stop_watch()
        broker.disconnect(user.id, websocket)
        db.close()
