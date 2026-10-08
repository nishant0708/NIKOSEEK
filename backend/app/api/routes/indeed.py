"""Indeed connection endpoints; mirrors the LinkedIn session API."""
from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.db.session import SessionLocal
from app.services.agent_config import indeed_public_payload
from app.services.indeed_browser import SOURCE, indeed_browser, session_payload
from app.services.linkedin_browser import get_browser_session

router = APIRouter(prefix="/indeed", tags=["indeed"])


def _payload(db, user):
    return {**session_payload(get_browser_session(db, user, SOURCE)), "credentials": indeed_public_payload(user)}


@router.get("/session")
def indeed_session(user: CurrentUser, db: DbSession) -> dict:
    return _payload(db, user)


@router.post("/session/connect")
async def connect_indeed(user: CurrentUser, db: DbSession) -> dict:
    indeed_browser.start_connect(db, user, SessionLocal)
    return _payload(db, user)


@router.post("/session/disconnect")
async def disconnect_indeed(user: CurrentUser, db: DbSession) -> dict:
    await indeed_browser.disconnect(db, user)
    return _payload(db, user)
