from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession
from app.services.agent_config import (
    ats_public_payload,
    indeed_public_payload,
    linkedin_public_payload,
    mark_ats_account,
    set_ats_credentials,
    set_indeed_credentials,
    set_linkedin_credentials,
)
from app.services.storage import PROFILE_TEMPLATE, read_profile_markdown, save_profile_markdown
from app.services.storage import read_details_markdown
from app.services.profile_generation import generate_profile_documents

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileMarkdown(BaseModel):
    markdown: str


class ProfileGenerationRequest(BaseModel):
    resume_key: str
    projects: str = ""
    achievements: str = ""


class LinkedInCredentialsRead(BaseModel):
    email: str = ""
    password_set: bool = False


class LinkedInCredentialsUpdate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str | None = Field(default=None, max_length=200)


class IndeedCredentialsRead(BaseModel):
    email: str = ""
    password_set: bool = False


class IndeedCredentialsUpdate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str | None = Field(default=None, max_length=200)


class AtsCredentialsRead(BaseModel):
    email: str = ""
    password_set: bool = False
    accounts: dict[str, dict[str, bool]] = {}


class AtsCredentialsUpdate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str | None = Field(default=None, max_length=200)


@router.get("")
def get_profile(user: CurrentUser) -> dict[str, str | None]:
    return {"markdown": read_profile_markdown(user.username)}


@router.get("/details")
def get_details(user: CurrentUser) -> dict[str, str | None]:
    return {"markdown": read_details_markdown(user.username)}


@router.get("/template")
def profile_template(user: CurrentUser) -> dict[str, str]:
    return {"markdown": PROFILE_TEMPLATE}


@router.put("")
def update_profile(payload: ProfileMarkdown, user: CurrentUser) -> dict[str, str]:
    save_profile_markdown(user.username, payload.markdown)
    return {"message": "Profile saved"}


@router.post("/generate")
def generate_profile(payload: ProfileGenerationRequest, user: CurrentUser) -> dict[str, str]:
    details, profile = generate_profile_documents(user.username, payload.resume_key, payload.projects, payload.achievements)
    return {"details_markdown": details, "profile_markdown": profile}


@router.get("/linkedin")
def get_linkedin_credentials(user: CurrentUser) -> LinkedInCredentialsRead:
    return LinkedInCredentialsRead.model_validate(linkedin_public_payload(user))


@router.put("/linkedin")
def update_linkedin_credentials(payload: LinkedInCredentialsUpdate, user: CurrentUser, db: DbSession) -> LinkedInCredentialsRead:
    current = linkedin_public_payload(user)
    if payload.password is not None and 0 < len(payload.password.strip()) < 6:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="LinkedIn password must be at least 6 characters")
    password = payload.password.strip() if payload.password and payload.password.strip() else None
    if not password and not current["password_set"]:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter a LinkedIn password to save credentials")
    set_linkedin_credentials(user, payload.email, password)
    db.commit()
    db.refresh(user)
    return LinkedInCredentialsRead.model_validate(linkedin_public_payload(user))


@router.get("/indeed")
def get_indeed_credentials(user: CurrentUser) -> IndeedCredentialsRead:
    return IndeedCredentialsRead.model_validate(indeed_public_payload(user))


@router.put("/indeed")
def update_indeed_credentials(payload: IndeedCredentialsUpdate, user: CurrentUser, db: DbSession) -> IndeedCredentialsRead:
    current = indeed_public_payload(user)
    if payload.password is not None and 0 < len(payload.password.strip()) < 6:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Indeed password must be at least 6 characters")
    password = payload.password.strip() if payload.password and payload.password.strip() else None
    # Indeed uses an email-first passkey flow. A password is accepted only for
    # existing accounts that choose to retain one; it is never required.
    set_indeed_credentials(user, payload.email, password)
    db.commit()
    db.refresh(user)
    return IndeedCredentialsRead.model_validate(indeed_public_payload(user))


@router.get("/ats")
def get_ats_credentials(user: CurrentUser) -> AtsCredentialsRead:
    """The ONE email + password the agent uses to sign up / sign in on company job portals (Workday, iCIMS, ...)."""
    return AtsCredentialsRead.model_validate(ats_public_payload(user))


@router.put("/ats")
def update_ats_credentials(payload: AtsCredentialsUpdate, user: CurrentUser, db: DbSession) -> AtsCredentialsRead:
    current = ats_public_payload(user)
    password = payload.password.strip() if payload.password and payload.password.strip() else None
    if password and len(password) < 8:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Use at least 8 characters — most portals also want upper + lower case, a digit and a symbol")
    if not password and not current["password_set"]:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter a password for your job-portal accounts")
    set_ats_credentials(user, payload.email, password)
    db.commit()
    db.refresh(user)
    return AtsCredentialsRead.model_validate(ats_public_payload(user))


@router.delete("/ats/accounts/{vendor}/{tenant}")
def forget_ats_account(vendor: str, tenant: str, user: CurrentUser, db: DbSession) -> AtsCredentialsRead:
    """Forget that a company already has an account (the agent will try to sign up there again)."""
    mark_ats_account(user, vendor, tenant, signed_up=False)
    db.refresh(user)
    return AtsCredentialsRead.model_validate(ats_public_payload(user))


@router.post("/upload")
def upload_profile(user: CurrentUser, file: UploadFile = File(...)) -> dict[str, str]:
    if not (file.filename or "").lower().endswith((".md", ".markdown", ".txt")):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload a Markdown file")
    try:
        markdown = file.file.read(200 * 1024 + 1).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Profile must be UTF-8 text") from exc
    if not markdown.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Profile is empty")
    save_profile_markdown(user.username, markdown)
    return {"message": "Profile saved"}
