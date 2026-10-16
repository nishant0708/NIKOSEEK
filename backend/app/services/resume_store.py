"""Resume metadata (DB) on top of the resume objects in Supabase Storage.

Storage stays the source of the files (`<user>/Resumes/<category>/resume_<digest>.pdf`); the `resumes` table adds
what the resume agent needs: a label ("Python-heavy backend"), origin (uploaded / generated), cached text for
scoring, the structured content of generated resumes, and the file name used on LinkedIn / Indeed.
`resume_portal_uploads` remembers which resume already sits in your LinkedIn / Indeed account (and under what name),
so the agent selects it there instead of uploading it again.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Resume, ResumePortalUpload, User
from app.db.session import SessionLocal
from app.services.storage import download_file, list_resumes, read_profile_markdown, resume_role_of

logger = logging.getLogger(__name__)

PORTALS = ("linkedin", "indeed")
_NAME_PART = re.compile(r"[^A-Za-z0-9]+")


def _title(slug: str) -> str:
    return " ".join(word.capitalize() for word in re.split(r"[-_\s]+", slug or "") if word) or "Resume"


def applicant_name(username: str) -> str:
    """'Nishant Kaushal' from the profile heading (`# Nishant Kaushal`), else the username."""
    profile = read_profile_markdown(username) or ""
    match = re.search(r"^#\s+(.+)$", profile, re.M)
    name = match.group(1).strip() if match else username
    return name if 2 <= len(name) <= 60 and "full name" not in name.lower() else username


def _file_stem(*parts: str) -> str:
    words = [w for part in parts for w in _NAME_PART.split(part or "") if w]
    return "_".join(words)[:90] or "Resume"


def unique_upload_name(db: Session, user_id: int, stem: str, extension: str) -> str:
    """LinkedIn / Indeed list your resumes by file name, so every variant needs its own."""
    taken = {name.lower() for (name,) in db.query(Resume.upload_name).filter(Resume.user_id == user_id).all()}
    candidate, n = f"{stem}{extension}", 2
    while candidate.lower() in taken:
        candidate, n = f"{stem}_{n}{extension}", n + 1
    return candidate


def extract_text(storage_key: str) -> str:
    from app.services.profile_generation import _extract_resume_text

    return _extract_resume_text(Path(storage_key).suffix.lower(), download_file(storage_key))


def sync_resumes(db: Session, user: User) -> list[Resume]:
    """Rows for every resume object in storage (creates rows for files uploaded before the resume agent existed).

    Returns only resumes whose file still exists, oldest first.
    """
    objects = list_resumes(user.username)
    keys = {item["storage_key"] for item in objects}
    rows = {row.storage_key: row for row in db.query(Resume).filter(Resume.user_id == user.id).all()}
    name = None
    for item in objects:
        if item["storage_key"] in rows:
            continue
        name = name or applicant_name(user.username)
        role = item.get("role") or "resume"
        extension = Path(item["storage_key"]).suffix.lower() or ".pdf"
        row = Resume(
            user_id=user.id, storage_key=item["storage_key"], category=role, label=f"{_title(role)} (original)", origin="uploaded",
            upload_name=unique_upload_name(db, user.id, _file_stem(name, _title(role)), extension), content={},
        )
        try:
            with db.begin_nested():  # another request / the worker may sync the same file at the same moment
                db.add(row)
                db.flush()
            rows[row.storage_key] = row
        except IntegrityError:
            existing = db.query(Resume).filter(Resume.user_id == user.id, Resume.storage_key == item["storage_key"]).one_or_none()
            if existing is not None:
                rows[existing.storage_key] = existing
    db.commit()
    return sorted((row for key, row in rows.items() if key in keys), key=lambda r: (r.created_at or datetime.now(UTC), r.id))



def get_resume(db: Session, user: User, storage_key: str) -> Resume | None:
    return db.query(Resume).filter(Resume.user_id == user.id, Resume.storage_key == storage_key).one_or_none()


def serialize_resume(row: Resume, uploads: list[ResumePortalUpload] | None = None) -> dict:
    uploads = uploads or []
    return {
        "id": row.id, "storage_key": row.storage_key, "role": row.category, "category": row.category,
        "file_name": Path(row.storage_key).name, "label": row.label, "origin": row.origin, "upload_name": row.upload_name,
        "parent_id": row.parent_id, "source_job_id": row.source_job_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        # Each board may show a different filename for the same stored resume.
        "portals": {u.portal: u.display_name for u in uploads},
        "sources": [{"source": u.portal, "display_name": u.display_name,
                     "uploaded_at": u.uploaded_at.isoformat() if u.uploaded_at else None,
                     "last_used_at": u.last_used_at.isoformat() if u.last_used_at else None}
                    for u in uploads],
    }


def list_resume_rows(db: Session, user: User) -> list[dict]:
    rows = sync_resumes(db, user)
    uploads = db.query(ResumePortalUpload).filter(ResumePortalUpload.user_id == user.id).all()
    by_resume: dict[int, list[ResumePortalUpload]] = {}
    for upload in uploads:
        by_resume.setdefault(upload.resume_id, []).append(upload)
    return [serialize_resume(row, by_resume.get(row.id)) for row in rows]


def categories(db: Session, user: User) -> list[str]:
    return sorted({row.category for row in sync_resumes(db, user)})


# ---------------------------------------------------------------------------
# Portal memory (called from the browser worker, so each call opens its own short DB session)
# ---------------------------------------------------------------------------
def upload_name_for(user_id: int, storage_key: str) -> str | None:
    with SessionLocal() as db:
        row = db.query(Resume).filter(Resume.user_id == user_id, Resume.storage_key == storage_key).one_or_none()
        return row.upload_name if row else None


def portal_display_name(user_id: int, storage_key: str, portal: str) -> str | None:
    with SessionLocal() as db:
        found = (
            db.query(ResumePortalUpload.display_name)
            .join(Resume, Resume.id == ResumePortalUpload.resume_id)
            .filter(Resume.user_id == user_id, Resume.storage_key == storage_key, ResumePortalUpload.portal == portal)
            .first()
        )
        return found[0] if found else None


def record_portal_upload(user_id: int, storage_key: str, portal: str, display_name: str, *, uploaded: bool) -> None:
    """Remember (or refresh) that this resume is in your `portal` account as `display_name`."""
    if portal not in PORTALS or not display_name:
        return
    with SessionLocal() as db:
        row = db.query(Resume).filter(Resume.user_id == user_id, Resume.storage_key == storage_key).one_or_none()
        if row is None:
            return
        record = db.query(ResumePortalUpload).filter(ResumePortalUpload.resume_id == row.id, ResumePortalUpload.portal == portal).one_or_none()
        now = datetime.now(UTC)
        if record is None:
            record = ResumePortalUpload(user_id=user_id, resume_id=row.id, portal=portal, display_name=display_name[:255])
            db.add(record)
        else:
            record.display_name = display_name[:255]
        if uploaded:
            record.uploaded_at = now
        record.last_used_at = now
        db.commit()


def forget_portal_upload(user_id: int, storage_key: str, portal: str) -> None:
    """The portal no longer lists it (you deleted it there): upload again next time."""
    with SessionLocal() as db:
        row = db.query(Resume).filter(Resume.user_id == user_id, Resume.storage_key == storage_key).one_or_none()
        if row is not None:
            db.query(ResumePortalUpload).filter(ResumePortalUpload.resume_id == row.id, ResumePortalUpload.portal == portal).delete()
            db.commit()


def role_of(storage_key: str) -> str | None:
    return resume_role_of(storage_key)


def category_slug(value) -> str | None:
    """'Backend Developer' -> 'backend-developer' (same shape as the storage folder names)."""
    if not isinstance(value, str):
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")[:60]
    return slug or None


# Standard role families offered to the categoriser next to your own folders, so a frontend job is filed as frontend
# even when you only have a backend resume (the agent then tailors one into a new frontend-developer folder).
STANDARD_CATEGORIES = (
    "frontend-developer", "backend-developer", "full-stack-developer", "mobile-developer",
    "devops-engineer", "data-engineer", "ml-engineer", "qa-engineer",
)


def category_choices(existing: list[str] | None) -> list[str]:
    """Your folders first (exact names), then the standard families you don't have yet."""
    own = [c for c in dict.fromkeys(category_slug(c) or c for c in (existing or []))]
    return own + [c for c in STANDARD_CATEGORIES if c not in own]
