"""Resume agent — picks (or builds) the best resume for a job before the apply agent opens it.

Three scorers work together:
  1. Profile relevance   job_matching.py (search time) — accepts / rejects jobs against profile.md
  2. Job category        same search-time call returns `category` (a resume folder: backend-developer, frontend-developer, ...)
                         when the resume agent is ON; `job_categorization` prompt is the apply-time fallback
  3. JD <-> resume       `resume_scoring` prompt — scores every resume in that category against the real job description

Decision (thresholds editable on the Workflow page):
  best >= reuse_threshold (75)          -> apply with it, nothing generated
  tailor_min (55) <= best < reuse       -> `resume_tailoring` builds a tuned resume from profile.md + details.md
                                           (+ the best resume as reference), guard strips anything not in the
                                           profile, re-score; up to max_rounds; the highest score wins
  best < tailor_min                     -> weak fit: apply with the best existing one (no tokens spent on tailoring)

Generated resumes are saved in the same category folder (Resumes/<category>/) with a label like "Python-heavy backend",
so later jobs in that category are scored against them too. Choosing a resume yourself (Retry / Switch resume) pins it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db.models import Application, Resume, ScrapedJob, SearchRun, User
from app.services.job_requirements import experience_gate
from app.services.agent_config import fresh_config, get_match_scoring_model, save_config
from app.services.llm import generate_json_object
from app.services.prompts import render
from app.services.resume_render import render_pdf, resume_text
from app.services.resume_store import (
    _file_stem, _title, applicant_name, category_choices, category_slug, extract_text, sync_resumes, unique_upload_name,
)
from app.services.storage import read_details_markdown, read_profile_markdown, save_generated_resume

logger = logging.getLogger(__name__)

DEFAULTS = {"enabled": False, "reuse_threshold": 75, "tailor_min": 55, "max_rounds": 2, "model": ""}
MAX_CANDIDATES = 6  # resumes scored per job (bounded token use as generated variants pile up)
JD_CHARS = 9000
RESUME_CHARS = 7000


# ---------------------------------------------------------------------------
# Settings (users.agent_config["resume_agent"])
# ---------------------------------------------------------------------------
def resume_agent_settings(user: User) -> dict:
    stored = (user.agent_config or {}).get("resume_agent")
    stored = stored if isinstance(stored, dict) else {}
    merged = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    merged["enabled"] = bool(merged["enabled"])
    return merged


def validate_settings(values: dict) -> dict:
    out = {**DEFAULTS, **{k: v for k, v in values.items() if k in DEFAULTS}}
    out["enabled"] = bool(out["enabled"])
    out["reuse_threshold"] = int(out["reuse_threshold"])
    out["tailor_min"] = int(out["tailor_min"])
    out["max_rounds"] = int(out["max_rounds"])
    out["model"] = str(out.get("model") or "").strip()[:120]
    if not 0 <= out["tailor_min"] < out["reuse_threshold"] <= 100:
        raise ValueError("Need 0 ≤ tailor minimum < reuse threshold ≤ 100")
    if not 1 <= out["max_rounds"] <= 3:
        raise ValueError("Tailoring rounds must be between 1 and 3")
    return out


def set_resume_agent_settings(user: User, values: dict) -> dict:
    clean = validate_settings({**resume_agent_settings(user), **values})
    config = fresh_config(user)
    config["resume_agent"] = clean
    save_config(user, config)
    return clean


def _model(user: User, settings: dict) -> str:
    return settings.get("model") or get_match_scoring_model(user)


# ---------------------------------------------------------------------------
# Truth guard: everything in a generated resume must be backed by the applicant's own documents
# ---------------------------------------------------------------------------
def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", (value or "").lower())


class SourceIndex:
    """Fast 'does the profile mention this?' checks, tolerant to spacing / punctuation (Node.js == NodeJS)."""

    def __init__(self, text: str) -> None:
        self.raw = text or ""
        self.lower = self.raw.lower()
        self.compact = _compact(self.raw)
        self.tokens = {_compact(t) for t in re.findall(r"[a-z0-9+#][a-z0-9+#.\-]*", self.lower)}
        self.digits = re.sub(r"\D", "", self.raw)

    def supports(self, phrase) -> bool:
        if not isinstance(phrase, str):
            return False
        c = _compact(phrase)
        if not c:
            return False
        if len(c) <= 3:  # "Go", "C", "R", "AWS": whole tokens only, never a substring of another word
            return c in self.tokens
        if c in self.compact:
            return True
        return len(c) > 4 and c.endswith("s") and c[:-1] in self.compact  # "REST APIs" vs "REST API"

    def numbers_ok(self, text: str) -> bool:
        """Every number in a bullet (40%, 3x, 2023 …) must appear in the profile — no invented metrics."""
        for number in re.findall(r"\d+(?:\.\d+)?", text or ""):
            if not re.search(r"(?<![\d.])" + re.escape(number) + r"(?![\d])", self.raw):
                return False
        return True

    def contact_ok(self, value: str) -> bool:
        value = (value or "").strip()
        if not value:
            return False
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 7 and not re.search(r"[a-z]", value.lower()):
            return digits[-10:] in self.digits
        return value.lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip("/") in self.lower


def _strs(values) -> list[str]:
    return [str(v).strip() for v in (values or []) if isinstance(v, (str, int, float)) and str(v).strip()]


def guard_resume(content: dict, source_text: str, name: str) -> tuple[dict, list[str]]:
    """Strip anything the profile does not support. Returns (clean resume, human-readable list of what was removed)."""
    src = SourceIndex(source_text)
    removed: list[str] = []
    c = content if isinstance(content, dict) else {}

    def keep_bullets(bullets, where: str) -> list[str]:
        out = []
        for bullet in _strs(bullets):
            if src.numbers_ok(bullet):
                out.append(bullet)
            else:
                removed.append(f"bullet with a number not in your profile ({where}): {bullet[:80]}")
        return out[:6]

    def keep_years(value: str, where: str) -> str:
        value = str(value or "").strip()
        if value and not all(y in src.raw for y in re.findall(r"(?:19|20)\d{2}", value)):
            removed.append(f"date {value} ({where})")
            return ""
        return value

    contact = c.get("contact") if isinstance(c.get("contact"), dict) else {}
    clean_contact = {
        "email": contact.get("email") if src.contact_ok(str(contact.get("email") or "")) else "",
        "phone": contact.get("phone") if src.contact_ok(str(contact.get("phone") or "")) else "",
        "location": contact.get("location") if src.supports(str(contact.get("location") or "")) else "",
        "links": [link for link in _strs(contact.get("links")) if src.contact_ok(link)],
    }

    skills = []
    for group in c.get("skills") or []:
        if not isinstance(group, dict):
            continue
        items = []
        for item in _strs(group.get("items")):
            (items.append(item) if src.supports(item) else removed.append(f"skill {item}"))
        if items:
            skills.append({"group": str(group.get("group") or "Skills")[:40], "items": list(dict.fromkeys(items))[:20]})

    experience = []
    for item in c.get("experience") or []:
        if not isinstance(item, dict):
            continue
        company, title = str(item.get("company") or ""), str(item.get("title") or "")
        if not src.supports(company) or not src.supports(title):
            removed.append(f"role {title} at {company}")
            continue
        experience.append({
            "title": title, "company": company,
            "location": item.get("location") if src.supports(str(item.get("location") or "")) else "",
            "start": keep_years(item.get("start"), company), "end": keep_years(item.get("end"), company),
            "bullets": keep_bullets(item.get("bullets"), company),
        })

    projects = []
    for item in c.get("projects") or []:
        if not isinstance(item, dict):
            continue
        project = str(item.get("name") or "")
        if not src.supports(project):
            removed.append(f"project {project}")
            continue
        tech = []
        for t in _strs(item.get("tech")):
            (tech.append(t) if src.supports(t) else removed.append(f"tech {t} ({project})"))
        projects.append({
            "name": project, "tech": tech[:10], "link": item.get("link") if src.contact_ok(str(item.get("link") or "")) else "",
            "bullets": keep_bullets(item.get("bullets"), project),
        })

    education = []
    for item in c.get("education") or []:
        if not isinstance(item, dict):
            continue
        school = str(item.get("school") or "")
        if not src.supports(school):
            removed.append(f"education {school}")
            continue
        education.append({
            "school": school, "degree": str(item.get("degree") or ""),
            "start": keep_years(item.get("start"), school), "end": keep_years(item.get("end"), school),
            "grade": item.get("grade") if src.numbers_ok(str(item.get("grade") or "")) else "",
        })

    certifications = []
    for cert in _strs(c.get("certifications")):
        (certifications.append(cert) if src.supports(cert) else removed.append(f"certification {cert}"))
    achievements = []
    for achievement in _strs(c.get("achievements")):
        (achievements.append(achievement) if src.numbers_ok(achievement) else removed.append(f"achievement {achievement[:60]}"))

    summary_sentences = [s for s in re.split(r"(?<=[.!?])\s+", str(c.get("summary") or "").strip()) if s]
    summary = " ".join(s for s in summary_sentences if src.numbers_ok(s))
    if len(summary) < len(" ".join(summary_sentences)):
        removed.append("summary sentence with a number not in your profile")

    clean = {
        "label": str(c.get("label") or "").strip()[:60],
        "name": name,
        "headline": str(c.get("headline") or "").strip()[:120] if src.numbers_ok(str(c.get("headline") or "")) else "",
        "contact": clean_contact, "summary": summary[:700], "skills": skills, "experience": experience, "projects": projects,
        "education": education, "certifications": certifications[:8], "achievements": achievements[:6],
    }
    return clean, removed


# ---------------------------------------------------------------------------
# LLM steps
# ---------------------------------------------------------------------------
def _jd(job: ScrapedJob) -> str:
    return (job.description or job.title or "")[:JD_CHARS]


async def categorize_job(user: User, job: ScrapedJob, categories: list[str], model: str) -> str:
    """Scorer 2 fallback (jobs scored before the resume agent was turned on)."""
    if job.resume_category:
        return job.resume_category
    data = await generate_json_object(render(
        user, "job_categorization", job_title=job.title or "", job_description=_jd(job)[:4000],
        categories=", ".join(categories) or "(none yet)"), model=model)
    return category_slug(data.get("category")) or (categories[0] if categories else "resume")


async def score_resumes(user: User, job: ScrapedJob, candidates: list[tuple[int, str, str]], model: str) -> tuple[dict[int, dict], list[str]]:
    """Scorer 3. candidates = [(local id, label, text)] -> ({id: {score, matched, missing, reason}}, jd keywords)."""
    blocks = "\n\n".join(f"### Resume id={cid} ({label})\n{text[:RESUME_CHARS]}" for cid, label, text in candidates)
    data = await generate_json_object(render(
        user, "resume_scoring", job_title=job.title or "", job_company=job.company or "",
        job_description=_jd(job), resumes=blocks), model=model)
    out: dict[int, dict] = {}
    for item in data.get("scores") or []:
        if not isinstance(item, dict):
            continue
        try:
            cid = int(item.get("id"))
            score = max(0.0, min(100.0, float(item.get("score", 0))))
        except (TypeError, ValueError):
            continue
        out[cid] = {"score": round(score, 1), "matched": _strs(item.get("matched"))[:25], "missing": _strs(item.get("missing"))[:25],
                    "reason": str(item.get("reason") or "")[:300]}
    for cid, _, _ in candidates:
        out.setdefault(cid, {"score": 0.0, "matched": [], "missing": [], "reason": "No score returned"})
    return out, _strs(data.get("jd_keywords"))[:25]


async def tailor_resume(user: User, job: ScrapedJob, category: str, profile: str, base: str, keywords: list[str], feedback: str, model: str) -> dict:
    return await generate_json_object(render(
        user, "resume_tailoring", job_title=job.title or "", job_company=job.company or "", job_description=_jd(job),
        category=category, profile=profile[:20000], base_resume=base[:RESUME_CHARS],
        keywords=", ".join(keywords) or "(none — focus on choosing and ordering the most relevant projects and bullets)",
        feedback=feedback or "(first round)"), model=model)


# ---------------------------------------------------------------------------
# Orchestration (runs as the first stage of every application while the agent is ON)
# ---------------------------------------------------------------------------
def _pick_candidates(rows: list[Resume], category: str) -> list[Resume]:
    in_category = [r for r in rows if category_slug(r.category) == category] or rows
    originals = [r for r in in_category if r.origin != "generated"]
    generated = sorted((r for r in in_category if r.origin == "generated"), key=lambda r: r.id, reverse=True)
    return (originals + generated)[:MAX_CANDIDATES]


def _use(db: Session, application: Application, row: Resume) -> None:
    application.resume_key = row.storage_key
    application.resume_role = row.category
    application.job.resume_key = row.storage_key
    application.job.resume_role = row.category
    db.commit()


JD_SELECTORS = (
    "#jobDescriptionText",                     # Indeed
    ".jobs-description__content", ".jobs-box__html-content", "#job-details",  # LinkedIn (classic)
    '[class*="jobs-description"]', '[data-testid="expandable-text-box"]',     # LinkedIn (2026)
)


async def read_jd_from_page(page) -> str | None:
    """The job description text from the open posting tab (for jobs scraped without one)."""
    for selector in JD_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                text = (await locator.inner_text(timeout=3000)).strip()
                if len(text) > 200:
                    return text[:12000]
        except Exception:
            continue
    try:
        body = await page.inner_text("body", timeout=5000)
    except Exception:
        return None
    start = body.find("About the job")
    return body[start:start + 9000].strip() if start >= 0 else None


async def run_resume_agent(db: Session, user: User, application: Application, log, page=None) -> dict | None:
    """Choose / build the resume for this application. Returns the decision (also saved on application.resume_agent).

    `page`: the open job posting. When the job was saved without a description (older LinkedIn searches),
    the JD is read from the page first, and the job is re-categorised from it."""
    settings = resume_agent_settings(user)
    if not settings["enabled"]:
        return None
    job = application.job
    state = dict(application.resume_agent or {})
    if state.get("pinned"):
        await log("info", "You picked this resume yourself — the resume agent keeps it.", data={"resume_key": application.resume_key})
        return state
    model = _model(user, settings)
    reuse, tailor_min = settings["reuse_threshold"], settings["tailor_min"]

    fetched_jd = False
    if not job.description and page is not None:
        text = await read_jd_from_page(page)
        if text:
            job.description = text
            job.resume_category = None  # it was guessed from the title only
            db.commit()
            fetched_jd = True
            await log("info", f"Read the job description from the posting ({len(text)} characters) — it was missing from the search results.")
    if job.description and job.search_run_id:
        run = db.get(SearchRun, job.search_run_id)
        wanted = (run.parameters or {}).get("experience") if run else None
        too_senior = experience_gate(job.description, wanted)
        if too_senior:
            await log("warning", f"{too_senior} Scores below will reflect that.")

    rows = await asyncio.to_thread(load_resume_rows, user.id)
    rows = [db.merge(r) for r in rows]
    if not rows:
        await log("warning", "No resumes uploaded yet — the resume agent has nothing to score.")
        return None

    category = await categorize_job(user, job, category_choices([r.category for r in rows]), model)
    if job.resume_category != category:
        job.resume_category = category
        db.commit()
    if fetched_jd:
        await log("info", f"Category from the real JD: {category}.")
    candidates = _pick_candidates(rows, category)
    await log("info", f"Job category: {category}. Scoring {len(candidates)} resume(s): " + ", ".join(r.label for r in candidates) + ".",
              data={"category": category, "candidates": [r.label for r in candidates]})

    for row in candidates:
        if not row.text:
            try:
                row.text = (await asyncio.to_thread(extract_text, row.storage_key))[:30000]
                db.commit()
            except Exception as exc:
                await log("warning", f"Could not read {row.label}: {str(exc)[:120]}")
    scorable = [r for r in candidates if r.text]
    if not scorable:
        await log("warning", "Could not read any resume text — keeping the current resume.")
        return None
    if not job.description:
        await log("warning", "No job description found (not in the search results, not readable on the page) — scores are based on the title only.")

    scores, jd_keywords = await score_resumes(user, job, [(i, r.label, r.text or "") for i, r in enumerate(scorable, 1)], model)
    ranked = sorted(((scores[i], r) for i, r in enumerate(scorable, 1)), key=lambda pair: pair[0]["score"], reverse=True)
    best_score, best_row = ranked[0]
    candidate_view = [{"resume_key": r.storage_key, "label": r.label, "origin": r.origin, **s} for s, r in ranked]
    await log("thinking", "Resume scores vs this JD: " + "; ".join(f"{r.label} {s['score']:.0f}" for s, r in ranked) + ".",
              data={"candidates": candidate_view, "jd_keywords": jd_keywords})

    decision = {
        "category": category, "candidates": candidate_view, "jd_keywords": jd_keywords, "thresholds": {"reuse": reuse, "tailor_min": tailor_min},
        "base_key": best_row.storage_key, "base_score": best_score["score"], "decided_at": datetime.now(UTC).isoformat(),
    }

    if best_score["score"] >= reuse:
        decision.update(status="reused", chosen_key=best_row.storage_key, chosen_label=best_row.label, chosen_score=best_score["score"])
        await log("action", f"Using {best_row.label} ({best_score['score']:.0f} ≥ {reuse}) — no new resume needed.")
    elif best_score["score"] < tailor_min:
        decision.update(status="low_fit", chosen_key=best_row.storage_key, chosen_label=best_row.label, chosen_score=best_score["score"])
        await log("warning", f"Weak fit: best resume scores {best_score['score']:.0f} (< {tailor_min}). Applying with {best_row.label}; nothing generated.")
    else:
        decision.update(await _tailor_loop(db, user, application, log, settings, model, category, best_row, best_score))

    chosen = next((r for r in rows if r.storage_key == decision["chosen_key"]), None)
    if chosen is None:
        chosen = db.query(Resume).filter(Resume.user_id == user.id, Resume.storage_key == decision["chosen_key"]).one()
    _use(db, application, chosen)
    application.resume_agent = {**state, **decision, "pinned": False}
    db.commit()
    return application.resume_agent


def load_resume_rows(user_id: int) -> list[Resume]:
    """Synced resume rows, detached (safe to load in a worker thread)."""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        user = db.get(User, user_id)
        rows = sync_resumes(db, user)
        db.expunge_all()
        return rows


async def _tailor_loop(db: Session, user: User, application: Application, log, settings: dict, model: str,
                       category: str, base: Resume, base_score: dict) -> dict:
    job = application.job
    reuse = settings["reuse_threshold"]
    profile, details = await asyncio.gather(asyncio.to_thread(read_profile_markdown, user.username), asyncio.to_thread(read_details_markdown, user.username))
    profile_text = f"{profile or ''}\n\n{details or ''}"
    source = SourceIndex(f"{profile_text}\n\n{base.text or ''}")
    addable = [k for k in base_score["missing"] if source.supports(k)]
    not_yours = [k for k in base_score["missing"] if not source.supports(k)]
    await log("info", f"Tailoring from {base.label} ({base_score['score']:.0f}). "
              + (f"Can surface from your profile: {', '.join(addable)}. " if addable else "No missing keyword is in your profile; reordering projects/bullets only. ")
              + (f"Not in your profile, so NOT added: {', '.join(not_yours)}." if not_yours else ""),
              data={"addable": addable, "not_in_profile": not_yours})

    name = await asyncio.to_thread(applicant_name, user.username)
    best: tuple[dict, str, dict] | None = None
    feedback = ""
    for round_no in range(1, settings["max_rounds"] + 1):
        raw = await tailor_resume(user, job, category, profile_text, base.text or "", addable, feedback, model)
        content, removed = guard_resume(raw, source.raw, name)
        if removed:
            await log("warning", f"Round {round_no}: removed {len(removed)} item(s) not found in your profile.", data={"removed": removed[:40]})
        text = resume_text(content)
        scored, _ = await score_resumes(user, job, [(1, content.get("label") or "tailored", text)], model)
        result = scored[1]
        await log("thinking", f"Round {round_no}: tailored resume scored {result['score']:.0f} (base {base_score['score']:.0f}).",
                  data={"score": result["score"], "missing": result["missing"]})
        if best is None or result["score"] > best[2]["score"]:
            best = (content, text, result)
        if result["score"] >= reuse:
            break
        still = [k for k in result["missing"] if source.supports(k)]
        feedback = f"Scored {result['score']:.0f}/100. JD keywords from the profile still not visible: {', '.join(still) or 'none'}. {result['reason']}"

    if best is None or best[2]["score"] <= base_score["score"]:
        await log("info", f"Tailoring did not beat {base.label} — applying with it.")
        return {"status": "tailor_no_gain", "chosen_key": base.storage_key, "chosen_label": base.label, "chosen_score": base_score["score"],
                "tailored_score": best[2]["score"] if best else None}

    content, text, result = best
    pdf = await render_pdf(content)
    key = await asyncio.to_thread(save_generated_resume, user.username, category, pdf, ".pdf")
    label = content.get("label") or f"{_title(category)} — {job.company or 'tailored'}"
    row = db.query(Resume).filter(Resume.user_id == user.id, Resume.storage_key == key).one_or_none()
    if row is None:
        row = Resume(user_id=user.id, storage_key=key, category=category, label=label[:200], origin="generated", parent_id=base.id,
                     source_job_id=job.id, upload_name=unique_upload_name(db, user.id, _file_stem(name, _title(category), label), ".pdf"),
                     content=content, text=text)
        db.add(row)
        db.commit()
    added = [k for k in base_score["missing"] if SourceIndex(text).supports(k)]
    await log("action", f"Saved new resume “{row.label}” in {category} ({result['score']:.0f} vs {base_score['score']:.0f}).",
              data={"resume_key": key, "keywords_added": added, "upload_name": row.upload_name})
    return {"status": "generated", "chosen_key": key, "chosen_label": row.label, "chosen_score": result["score"],
            "generated_key": key, "keywords_added": added}


def pin_resume(application: Application, storage_key: str, label: str | None, by: str = "you") -> None:
    state = dict(application.resume_agent or {})
    state.update(pinned=True, chosen_key=storage_key, chosen_label=label or Path(storage_key).name, pinned_by=by,
                 pinned_at=datetime.now(UTC).isoformat())
    application.resume_agent = state
