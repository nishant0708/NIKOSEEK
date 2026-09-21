"""Private Supabase Storage integration.

Supabase Storage has virtual folders: common key prefixes form the user layout.
`.keep` objects make each intended prefix visible immediately after signup.
"""
import re
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from supabase import Client, create_client

from app.core.config import settings

SAFE_PART = re.compile(r"[^A-Za-z0-9_-]+")
ALLOWED_RESUME_TYPES = {
    ".pdf": (b"%PDF", "application/pdf"),
    ".docx": (b"PK\x03\x04", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}
PROFILE_TEMPLATE = """# Full Name

**Headline:** e.g. Backend Developer | Python, PostgreSQL
**Location:** City, Country

## Summary

## Skills

## Experience

## Projects

## Education

## Job Preferences
"""


def _client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _safe_part(value: str) -> str:
    clean = SAFE_PART.sub("-", value.strip().lower()).strip("-._")
    if not clean:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid username or role")
    return clean


def _key(username: str, *parts: str) -> str:
    return "/".join((_safe_part(username), *parts))


def _upload(key: str, data: bytes, content_type: str) -> None:
    try:
        _client().storage.from_(settings.supabase_storage_bucket).upload(
            key, data, {"content-type": content_type, "upsert": "true"}
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not save file to Supabase Storage") from exc


def initialise_user_storage(username: str) -> None:
    """Compatibility no-op.

    Supabase Storage folders are virtual. They appear automatically when the
    first profile or resume object is uploaded, so making empty `.keep` files
    during signup is both unnecessary and able to turn a successful account
    creation into a storage-related 502 response.
    """
    _safe_part(username)


def save_profile_markdown(username: str, markdown: str) -> str:
    data = markdown.encode("utf-8")
    if len(data) > 200 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Profile must be under 200 KB")
    key = _key(username, "Profile", "profile.md")
    _upload(key, data, "text/markdown; charset=utf-8")
    return key


def read_profile_markdown(username: str) -> str | None:
    key = _key(username, "Profile", "profile.md")
    try:
        return _client().storage.from_(settings.supabase_storage_bucket).download(key).decode("utf-8")
    except Exception:
        return None


def read_details_markdown(username: str) -> str | None:
    try:
        return _client().storage.from_(settings.supabase_storage_bucket).download(_key(username, "Profile", "details.md")).decode("utf-8")
    except Exception:
        return None


def save_details_markdown(username: str, markdown: str) -> str:
    key = _key(username, "Profile", "details.md")
    _upload(key, markdown.encode("utf-8"), "text/markdown; charset=utf-8")
    return key


def download_file(key: str) -> bytes:
    try:
        return _client().storage.from_(settings.supabase_storage_bucket).download(key)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not read resume from Supabase Storage") from exc


def save_resume(username: str, role: str, upload: UploadFile) -> str:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in ALLOWED_RESUME_TYPES:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Resume must be a PDF or DOCX file")
    data = upload.file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Resume must be under 5 MB")
    magic, content_type = ALLOWED_RESUME_TYPES[extension]
    if not data.startswith(magic):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File content does not match its extension")
    # The same role + file has a stable key, so retries and double-clicks do
    # not create multiple copies in private storage.
    digest = sha256(data).hexdigest()[:24]
    key = _key(username, "Resumes", _safe_part(role), f"resume_{digest}{extension}")
    _upload(key, data, content_type)
    return key


def has_resume(username: str) -> bool:
    """Return whether any role folder contains an uploaded resume object."""
    bucket = _client().storage.from_(settings.supabase_storage_bucket)
    try:
        roles = bucket.list(_key(username, "Resumes"))
        for role in roles:
            name = role.get("name", "")
            if name and name != ".keep":
                files = bucket.list(_key(username, "Resumes", name))
                if any(item.get("name", "").endswith((".pdf", ".docx")) for item in files):
                    return True
    except Exception:
        # A missing user prefix is equivalent to no uploaded resume.
        return False
    return False
