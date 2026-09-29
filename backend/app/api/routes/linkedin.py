"""LinkedIn connection: opens a real browser window so the user signs in once; later applications reuse it."""
from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.db.session import SessionLocal
from app.services.agent_config import linkedin_public_payload
from app.services.linkedin_browser import get_browser_session, linkedin_browser, session_payload

router = APIRouter(prefix="/linkedin", tags=["linkedin"])


def _payload(user, browser_session) -> dict:
    return {**session_payload(browser_session), "credentials": linkedin_public_payload(user)}


@router.get("/session")
def linkedin_session(user: CurrentUser, db: DbSession) -> dict:
    return _payload(user, get_browser_session(db, user))


@router.post("/session/connect")
async def connect_linkedin(user: CurrentUser, db: DbSession) -> dict:
    """Launches (or focuses) the user's LinkedIn window at /login. Poll GET /linkedin/session for READY."""
    browser_session = linkedin_browser.start_connect(db, user, SessionLocal)
    db.refresh(browser_session)
    return _payload(user, browser_session)


@router.post("/session/disconnect")
async def disconnect_linkedin(user: CurrentUser, db: DbSession) -> dict:
    browser_session = await linkedin_browser.disconnect(db, user)
    return _payload(user, browser_session)
