"""Persisted, live-streamed log of what the apply agent is doing and *why*.

Every entry is written to job_application_events and pushed over the websocket as
{"type": "application.event", "event": {...}} so the Live Workflow page updates instantly.
"""
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Application, ApplicationEvent
from app.services.llm import TokenMeter, current_meter

logger = logging.getLogger(__name__)

STAGE_KEYS = ("open", "login", "find", "fields", "review", "failed")


def serialize_event(event: ApplicationEvent) -> dict:
    return {
        "id": event.id,
        "application_id": event.application_id,
        "attempt": event.attempt,
        "stage_key": event.stage_key,
        "kind": event.kind,
        "message": event.message,
        "data": event.data or {},
        "created_at": event.created_at,
    }


def latest_attempt(db: Session, application_id: int) -> int:
    return db.query(func.coalesce(func.max(ApplicationEvent.attempt), 0)).filter(ApplicationEvent.application_id == application_id).scalar() or 0


def list_events(db: Session, application_id: int, attempt: int | None = None) -> tuple[int, list[ApplicationEvent]]:
    attempt = attempt or latest_attempt(db, application_id)
    events = (
        db.query(ApplicationEvent)
        .filter(ApplicationEvent.application_id == application_id, ApplicationEvent.attempt == attempt)
        .order_by(ApplicationEvent.id.asc())
        .all()
    )
    return attempt, events


class AgentLog:
    def __init__(self, db: Session, application: Application, websocket_broker, *, continue_attempt: bool = False, stage_key: str = "open") -> None:
        """New run -> next attempt number. continue_attempt=True appends to the latest attempt (user actions on an open form)."""
        self.db = db
        self.application = application
        self.broker = websocket_broker
        self.stage_key = stage_key
        latest = latest_attempt(db, application.id)
        self.attempt = max(latest, 1) if continue_attempt else latest + 1
        # Every AI call made from here on (same asyncio task) is counted and attached to the next log entry.
        self.meter = TokenMeter()
        current_meter.set(self.meter)

    def set_stage(self, stage_key: str) -> None:
        if stage_key in STAGE_KEYS:
            self.stage_key = stage_key

    async def __call__(self, kind: str, message: str, *, data: dict | None = None, stage_key: str | None = None) -> None:
        if stage_key:
            self.set_stage(stage_key)
        logger.info("[app %s][%s/%s] %s", self.application.id, self.stage_key, kind, message)
        usage = self.meter.take()
        if usage:
            data = {**(data or {}), "tokens": usage}
            app = self.application
            app.llm_input_tokens = (app.llm_input_tokens or 0) + usage["input"]
            app.llm_output_tokens = (app.llm_output_tokens or 0) + usage["output"]
            app.llm_total_tokens = (app.llm_total_tokens or 0) + usage["total"]
            app.llm_calls = (app.llm_calls or 0) + usage["calls"]
        try:
            event = ApplicationEvent(
                application_id=self.application.id,
                user_id=self.application.user_id,
                attempt=self.attempt,
                stage_key=self.stage_key,
                kind=kind,
                message=message[:4000],
                data=data or {},
            )
            self.db.add(event)
            self.db.commit()
            self.db.refresh(event)
            payload = serialize_event(event)
        except Exception:
            # Logging must never break an application run (e.g. table not migrated yet).
            logger.exception("Could not persist agent event")
            self.db.rollback()
            payload = {
                "id": None, "application_id": self.application.id, "attempt": self.attempt, "stage_key": self.stage_key,
                "kind": kind, "message": message, "data": data or {}, "created_at": None,
            }
        await self.broker.send_to_user(self.application.user_id, {"type": "application.event", "event": payload})
