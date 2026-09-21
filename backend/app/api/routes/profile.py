from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import CurrentUser
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
