from datetime import UTC, datetime
import inspect
import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.db.models import Application, ScrapedJob, SearchRun
from app.schemas.jobs import JobRead, JobScoreRequest, JobScrapeRequest, JobScrapeResponse
from app.services.agent_config import get_match_scoring_model, get_match_scoring_prompt
from app.services.job_matching import SCORING_BATCH_SIZE, attach_analysis, gate_experience, score_jobs, score_jobs_until_target
from app.services.llm import LLMUnavailableError
from app.services.resume_agent import resume_agent_settings
from app.services.resume_store import categories as resume_categories, category_choices
from app.services.storage import read_details_markdown, read_profile_markdown

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)
SUPPORTED_SOURCES = {"linkedin", "indeed"}


def row_value(row: object, name: str) -> str | None:
    item = row.get(name)  # type: ignore[attr-defined]
    return None if item is None or str(item) == "nan" else str(item)


def to_read(job: ScrapedJob) -> JobRead:
    # SQLAlchemy column defaults are applied during INSERT, while this helper
    # also handles new in-memory jobs before that point.
    return JobRead(id=job.id, source=job.source, external_url=job.external_url, title=job.title, company=job.company, location=job.location, description=job.description, date_posted=job.date_posted, ai_status=job.ai_status or "PENDING", match_score=job.match_score, ai_reason=job.ai_reason, experience_match=job.experience_match, resume_category=job.resume_category)


@router.post("/scrape", response_model=JobScrapeResponse)
def scrape_jobs(payload: JobScrapeRequest, user: CurrentUser, db: DbSession) -> JobScrapeResponse:
    unknown = set(payload.sources) - SUPPORTED_SOURCES
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only LinkedIn and Indeed are available in this phase")
    profile = read_profile_markdown(user.username)
    details = read_details_markdown(user.username)
    if not profile or not details:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Generate and review your profile before starting a job search")

    run = SearchRun(user_id=user.id, parameters=payload.model_dump())
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info("Search run %s started for user_id=%s, sources=%s, term=%r", run.id, user.id, payload.sources, payload.search_term)
    try:
        from jobspy import scrape_jobs as run_scraper
        logger.info("Search run %s: fetching jobs from JobSpy", run.id)
        scraper_args = {
            "site_name": payload.sources,
            "search_term": payload.search_term,
            "location": payload.location,
            # The target is accepted matches, so scrape a broader candidate pool.
            "results_wanted": min(100, max(payload.results_wanted, payload.target_relevant_count * 10)),
            "hours_old": payload.hours_old,
            "job_type": payload.job_type,
            "is_remote": bool(payload.is_remote),
            "verbose": 0,
            # Without this JobSpy returns LinkedIn jobs with NO description, so scoring only saw the title.
            "linkedin_fetch_description": True,
        }
        if "fetch_description" in inspect.signature(run_scraper).parameters:  # newer JobSpy name for the same switch
            scraper_args["fetch_description"] = True
        # JobSpy uses country_indeed to select Indeed's regional catalogue;
        # location alone is not enough for countries with the same city names.
        if "indeed" in payload.sources and payload.country_indeed:
            scraper_args["country_indeed"] = payload.country_indeed
        dataframe = run_scraper(**scraper_args)
        scraped: list[dict] = []
        for _, row in dataframe.iterrows():
            url = row_value(row, "job_url")
            if url:
                scraped.append({"source": row_value(row, "site") or payload.sources[0], "external_url": url, "title": row_value(row, "title"), "company": row_value(row, "company"), "location": row_value(row, "location"), "description": row_value(row, "description"), "date_posted": row_value(row, "date_posted")})
        logger.info("Search run %s: JobSpy returned %s usable jobs (%s with a description)", run.id, len(scraped), sum(1 for job in scraped if job["description"]))
        if not scraped:
            run.status = "SUCCEEDED"; run.completed_at = datetime.now(UTC)
            db.commit()
            return JobScrapeResponse(run_id=run.id, accepted_jobs=[], rejected_jobs=[], pending_jobs=[], fetched_count=0, saved_count=0)
        model = get_match_scoring_model(user)
        logger.info("Search run %s: scoring up to %s jobs until %s matches are accepted with model=%s", run.id, len(scraped), payload.target_relevant_count, model)
        # Resume agent ON: the same scoring call also files each job under one of your resume folders (scorer 2).
        categories = None
        if resume_agent_settings(user)["enabled"]:
            categories = category_choices(resume_categories(db, user))
        scoring_unavailable = False
        try:
            analyses, usage = score_jobs_until_target(scraped, profile, details, payload, model, get_match_scoring_prompt(db), categories)
        except LLMUnavailableError as exc:
            logger.warning("Search run %s: Gemini unavailable; saving %s fetched jobs without scores", run.id, len(scraped))
            analyses, usage = [], {"input": 0, "output": 0, "total": 0}
            scoring_unavailable = True
        by_number = {item.get("job_number"): item for item in analyses}
        accepted: list[JobRead] = []
        rejected: list[JobRead] = []
        pending: list[JobRead] = []
        saved_count = 0
        # `analyses` only contains jobs through the requested accepted-job
        # target. Do not persist them or issue more scoring calls afterward.
        for index, raw in enumerate(scraped if scoring_unavailable else scraped[:len(analyses)], start=1):
            if scoring_unavailable:
                job = ScrapedJob(user_id=user.id, search_run_id=run.id, **raw)
                job.ai_status = "PENDING"
                job.ai_reason = "Saved without a match score because AI models were unavailable."
                db.add(job)
                try:
                    db.commit(); db.refresh(job); saved_count += 1
                    pending.append(to_read(job))
                except IntegrityError:
                    db.rollback()
                continue
            analysis = by_number.get(index, {"accepted": False, "match_score": 0, "reason": "No score returned", "experience_match": "Unknown"})
            job = ScrapedJob(user_id=user.id, search_run_id=run.id, **raw)
            candidate = attach_analysis(to_read(job), analysis)
            job.ai_status, job.match_score, job.ai_reason, job.experience_match = candidate.ai_status, candidate.match_score, candidate.ai_reason, candidate.experience_match
            job.resume_category = candidate.resume_category
            # Gemini returns usage for the batch. Attribute it evenly so each job
            # retains a durable match-cost record without exposing it in the UI.
            job.llm_input_tokens = usage["input"] // max(len(analyses), 1)
            job.llm_output_tokens = usage["output"] // max(len(analyses), 1)
            job.llm_total_tokens = usage["total"] // max(len(analyses), 1)
            db.add(job)
            try:
                db.commit(); db.refresh(job); saved_count += 1
                (accepted if job.ai_status == "ACCEPTED" else rejected).append(to_read(job))
            except IntegrityError:
                db.rollback()
        run.status = "PARTIAL" if scoring_unavailable else "SUCCEEDED"
        run.error = "AI scoring unavailable; fetched jobs were saved as pending." if scoring_unavailable else None
        run.fetched_count = len(scraped); run.accepted_count = len(accepted); run.rejected_count = len(rejected); run.completed_at = datetime.now(UTC)
        run.llm_input_tokens = usage["input"]; run.llm_output_tokens = usage["output"]; run.llm_total_tokens = usage["total"]
        db.commit()
        logger.info("Search run %s succeeded: accepted=%s rejected=%s saved=%s tokens=%s", run.id, len(accepted), len(rejected), saved_count, usage["total"])
        return JobScrapeResponse(run_id=run.id, accepted_jobs=accepted, rejected_jobs=rejected, pending_jobs=pending, fetched_count=len(scraped), saved_count=saved_count)
    except HTTPException as exc:
        logger.exception("Search run %s failed with HTTP %s: %s", run.id, exc.status_code, exc.detail)
        run.status = "FAILED"; run.error = str(exc.detail); run.completed_at = datetime.now(UTC); db.commit()
        raise
    except Exception as exc:
        logger.exception("Search run %s failed unexpectedly", run.id)
        run.status = "FAILED"; run.error = str(exc)[:2000]; run.completed_at = datetime.now(UTC); db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Job search failed") from exc


QUEUED_APPLICATION_STATUSES = {"QUEUED", "IN_PROGRESS", "FAILED", "HUMAN_REVIEW_REQUIRED", "APPLIED", "SUCCEEDED"}


@router.get("", response_model=list[JobRead])
def list_jobs(user: CurrentUser, db: DbSession, status_filter: str | None = None) -> list[JobRead]:
    queued_job_ids = db.query(Application.job_id).filter(Application.user_id == user.id)
    query = db.query(ScrapedJob).filter(ScrapedJob.user_id == user.id, ~ScrapedJob.id.in_(queued_job_ids))
    query = query.filter(~ScrapedJob.application_status.in_(QUEUED_APPLICATION_STATUSES))
    if status_filter:
        query = query.filter(ScrapedJob.ai_status == status_filter.upper())
    return [to_read(job) for job in query.order_by(ScrapedJob.match_score.desc().nullslast(), ScrapedJob.scraped_at.desc()).all()]


def score_pending_jobs(job_ids: list[int], user: CurrentUser, db: DbSession) -> list[JobRead]:
    unique_ids = list(dict.fromkeys(job_ids))
    jobs = db.query(ScrapedJob).filter(ScrapedJob.id.in_(unique_ids), ScrapedJob.user_id == user.id).all()
    by_id = {job.id: job for job in jobs}
    if len(by_id) != len(unique_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more jobs were not found")
    if any(job.ai_status != "PENDING" for job in jobs):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="One or more jobs have already been scored")
    profile = read_profile_markdown(user.username)
    details = read_details_markdown(user.username)
    if not profile or not details:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Generate and review your profile before scoring jobs")
    runs = {run.id: run for run in db.query(SearchRun).filter(SearchRun.id.in_([j.search_run_id for j in jobs if j.search_run_id]), SearchRun.user_id == user.id).all()} if any(j.search_run_id for j in jobs) else {}
    categories = category_choices(resume_categories(db, user)) if resume_agent_settings(user)["enabled"] else None
    model, prompt = get_match_scoring_model(user), get_match_scoring_prompt(db)
    groups: dict[tuple, list[ScrapedJob]] = {}
    preferences_by_key: dict[tuple, JobScrapeRequest] = {}
    for job in jobs:
        run = runs.get(job.search_run_id)
        params = (run.parameters if run else {}) or {}
        preferences = JobScrapeRequest(
            sources=[job.source], search_term=params.get("search_term") or job.title or "Job",
            location=params.get("location") or job.location or "India", experience=params.get("experience") or "0-1 years",
            min_salary_lpa=params.get("min_salary_lpa"), is_remote=bool(params.get("is_remote", False)),
        )
        key = (tuple(preferences.sources), preferences.search_term, preferences.location, preferences.experience,
               preferences.min_salary_lpa, preferences.is_remote)
        groups.setdefault(key, []).append(job)
        preferences_by_key[key] = preferences
    try:
        for key, group in groups.items():
            for start in range(0, len(group), SCORING_BATCH_SIZE):
                chunk = group[start:start + SCORING_BATCH_SIZE]
                raw_jobs = [{"title": job.title, "company": job.company, "location": job.location, "description": job.description} for job in chunk]
                analyses, usage = score_jobs(raw_jobs, profile, details, preferences_by_key[key], model, prompt, categories)
                by_number = {analysis.get("job_number"): analysis for analysis in analyses}
                for index, job in enumerate(chunk, 1):
                    analysis = gate_experience(by_number.get(index, {"accepted": False, "match_score": 0, "reason": "No score returned", "experience_match": "Unknown"}), raw_jobs[index - 1], preferences_by_key[key].experience)
                    result = attach_analysis(to_read(job), analysis)
                    job.ai_status, job.match_score = result.ai_status, result.match_score
                    job.ai_reason, job.experience_match, job.resume_category = result.ai_reason, result.experience_match, result.resume_category
                    job.llm_input_tokens += usage["input"] // max(len(chunk), 1)
                    job.llm_output_tokens += usage["output"] // max(len(chunk), 1)
                    job.llm_total_tokens += usage["total"] // max(len(chunk), 1)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        logger.exception("Could not score pending jobs %s", unique_ids)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not score these jobs right now") from exc
    db.commit()
    for job in jobs:
        db.refresh(job)
    return [to_read(by_id[job_id]) for job_id in unique_ids]


@router.post("/score-pending", response_model=list[JobRead])
def score_pending_jobs_route(payload: JobScoreRequest, user: CurrentUser, db: DbSession) -> list[JobRead]:
    return score_pending_jobs(payload.job_ids, user, db)


@router.post("/{job_id}/score", response_model=JobRead)
def score_pending_job(job_id: int, user: CurrentUser, db: DbSession) -> JobRead:
    return score_pending_jobs([job_id], user, db)[0]


@router.get("/runs")
def list_runs(user: CurrentUser, db: DbSession) -> list[dict]:
    runs = db.query(SearchRun).filter(SearchRun.user_id == user.id).order_by(SearchRun.created_at.desc()).limit(20).all()
    return [{"id": run.id, "status": run.status, "fetched_count": run.fetched_count, "accepted_count": run.accepted_count, "rejected_count": run.rejected_count, "error": run.error, "created_at": run.created_at} for run in runs]
