from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import CurrentUser
from app.services.storage import has_resume, list_resumes, save_resume

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.get("/status")
def resume_status(user: CurrentUser) -> dict[str, bool]:
    return {"has_resume": has_resume(user.username)}


@router.get("")
def available_resumes(user: CurrentUser) -> list[dict[str, str]]:
    return list_resumes(user.username)


@router.post("", status_code=status.HTTP_201_CREATED)
def upload_resume(
    user: CurrentUser, role: str = Form(..., min_length=2, max_length=100), file: UploadFile = File(...)
) -> dict[str, str]:
    storage_key = save_resume(user.username, role, file)
    return {"message": "Resume uploaded", "storage_key": storage_key}
