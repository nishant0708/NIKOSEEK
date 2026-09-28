from __future__ import annotations

import asyncio

import jwt
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.db.models import Application, User
from app.schemas.applications import ApplicationQueueRequest, ApplicationQueueResponse, ApplicationRead
from app.services.applications import enqueue_applications, serialize_application
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


@router.post("/apply", response_model=ApplicationQueueResponse)
async def apply_jobs(payload: ApplicationQueueRequest, user: CurrentUser, db: DbSession, request: Request) -> ApplicationQueueResponse:
    try:
        applications = enqueue_applications(db, user, payload.job_ids, payload.resume_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    queue: asyncio.Queue[tuple[int, int]] = request.app.state.application_queue
    for application in applications:
        queue.put_nowait((user.id, application.id))
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
    try:
        snapshot = [application.model_dump(mode="json") for application in list_applications(user, db)]
        await websocket.send_json({"type": "snapshot", "applications": snapshot})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        broker.disconnect(user.id, websocket)
    finally:
        broker.disconnect(user.id, websocket)
        db.close()
