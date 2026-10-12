from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.services.resume_store import list_resume_rows
from app.services.storage import ALLOWED_RESUME_TYPES, download_file, has_resume, resume_belongs_to, save_resume

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.get("/status")
def resume_status(user: CurrentUser) -> dict[str, bool]:
    return {"has_resume": has_resume(user.username)}


@router.get("")
def available_resumes(user: CurrentUser, db: DbSession) -> list[dict]:
    """Your resumes with resume-agent metadata (label, uploaded / generated, names on LinkedIn / Indeed)."""
    return list_resume_rows(db, user)


@router.get("/file")
def resume_file(key: str, user: CurrentUser) -> Response:
    """The resume file itself, for the in-app preview. Only keys inside your own Resumes/ folder."""
    if not resume_belongs_to(user.username, key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    extension = "." + key.rsplit(".", 1)[-1].lower()
    content_type = ALLOWED_RESUME_TYPES[extension][1]
    return Response(
        content=download_file(key), media_type=content_type,
        headers={"Content-Disposition": "inline", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def upload_resume(
    user: CurrentUser, role: str = Form(..., min_length=2, max_length=100), file: UploadFile = File(...)
) -> dict[str, str]:
    storage_key = save_resume(user.username, role, file)
    return {"message": "Resume uploaded", "storage_key": storage_key}
