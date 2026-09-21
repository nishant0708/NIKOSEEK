from pathlib import Path

from fastapi import HTTPException, status
from pypdf import PdfReader
from docx import Document

from app.services.llm import generate_text
from app.services.storage import download_file, save_details_markdown, save_profile_markdown


def _extract_resume_text(filename: str, data: bytes) -> str:
    try:
        if filename.endswith(".pdf"):
            from io import BytesIO
            return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
        if filename.endswith(".docx"):
            from io import BytesIO
            return "\n".join(paragraph.text for paragraph in Document(BytesIO(data)).paragraphs)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not read text from this resume") from exc
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only PDF and DOCX resumes are supported")


def generate_profile_documents(username: str, resume_key: str, projects: str, achievements: str) -> tuple[str, str]:
    if not resume_key.startswith(f"{username.lower()}/Resumes/"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="That resume does not belong to you")
    resume_text = _extract_resume_text(Path(resume_key).suffix.lower(), download_file(resume_key))
    source = f"RESUME:\n{resume_text[:30000]}\n\nUSER PROJECT DETAILS:\n{projects}\n\nUSER ACHIEVEMENTS:\n{achievements}"
    details = generate_text(source, "Create details.md for a job seeker. Preserve facts only. Use Markdown headings for projects, achievements, skills, experience evidence and measurable impact. Do not invent information.")
    profile = generate_text(source + "\n\nGENERATED DETAILS:\n" + details, "Create a concise, editable profile.md for job matching. Include headline, summary, skills, experience, projects, education and job preferences. Preserve facts only; do not invent information.")
    save_details_markdown(username, details)
    save_profile_markdown(username, profile)
    return details, profile
