from datetime import UTC, datetime
import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.db.models import ScrapedJob, SearchRun
from app.schemas.jobs import JobRead, JobScrapeRequest, JobScrapeResponse
from app.services.agent_config import get_match_scoring_model, get_match_scoring_prompt
from app.services.job_matching import attach_analysis, score_jobs
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
    return JobRead(id=job.id, source=job.source, external_url=job.external_url, title=job.title, company=job.company, location=job.location, description=job.description, date_posted=job.date_posted, ai_status=job.ai_status or "PENDING", match_score=job.match_score, ai_reason=job.ai_reason, experience_match=job.experience_match)


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
        dataframe = run_scraper(site_name=payload.sources, search_term=payload.search_term, location=payload.location, results_wanted=payload.results_wanted, hours_old=payload.hours_old, job_type=payload.job_type, is_remote=bool(payload.is_remote), verbose=0)
        scraped: list[dict] = []
        for _, row in dataframe.iterrows():
            url = row_value(row, "job_url")
            if url:
                scraped.append({"source": row_value(row, "site") or payload.sources[0], "external_url": url, "title": row_value(row, "title"), "company": row_value(row, "company"), "location": row_value(row, "location"), "description": row_value(row, "description"), "date_posted": row_value(row, "date_posted")})
        logger.info("Search run %s: JobSpy returned %s usable jobs", run.id, len(scraped))
        if not scraped:
            run.status = "SUCCEEDED"; run.completed_at = datetime.now(UTC)
            db.commit()
            return JobScrapeResponse(run_id=run.id, accepted_jobs=[], rejected_jobs=[], fetched_count=0, saved_count=0)
        model = get_match_scoring_model(user)
        logger.info("Search run %s: scoring %s jobs with model=%s", run.id, len(scraped), model)
        analyses, usage = score_jobs(scraped, profile, details, payload, model, get_match_scoring_prompt(db))
        by_number = {item.get("job_number"): item for item in analyses}
        accepted: list[JobRead] = []
        rejected: list[JobRead] = []
        saved_count = 0
        for index, raw in enumerate(scraped, start=1):
            analysis = by_number.get(index, {"accepted": False, "match_score": 0, "reason": "No score returned", "experience_match": "Unknown"})
            is_accepted = bool(analysis.get("accepted"))
            if is_accepted and len(accepted) >= payload.target_relevant_count:
                analysis = {**analysis, "accepted": False, "reason": "Relevant-job target reached for this search"}
            job = ScrapedJob(user_id=user.id, search_run_id=run.id, **raw)
            candidate = attach_analysis(to_read(job), analysis)
            job.ai_status, job.match_score, job.ai_reason, job.experience_match = candidate.ai_status, candidate.match_score, candidate.ai_reason, candidate.experience_match
            # Gemini returns usage for the batch. Attribute it evenly so each job
            # retains a durable match-cost record without exposing it in the UI.
            job.llm_input_tokens = usage["input"] // max(len(scraped), 1)
            job.llm_output_tokens = usage["output"] // max(len(scraped), 1)
            job.llm_total_tokens = usage["total"] // max(len(scraped), 1)
            db.add(job)
            try:
                db.commit(); db.refresh(job); saved_count += 1
                (accepted if job.ai_status == "ACCEPTED" else rejected).append(to_read(job))
            except IntegrityError:
                db.rollback()
        run.status = "SUCCEEDED"; run.fetched_count = len(scraped); run.accepted_count = len(accepted); run.rejected_count = len(rejected); run.completed_at = datetime.now(UTC)
        run.llm_input_tokens = usage["input"]; run.llm_output_tokens = usage["output"]; run.llm_total_tokens = usage["total"]
        db.commit()
        logger.info("Search run %s succeeded: accepted=%s rejected=%s saved=%s tokens=%s", run.id, len(accepted), len(rejected), saved_count, usage["total"])
        return JobScrapeResponse(run_id=run.id, accepted_jobs=accepted, rejected_jobs=rejected, fetched_count=len(scraped), saved_count=saved_count)
    except HTTPException as exc:
        logger.exception("Search run %s failed with HTTP %s: %s", run.id, exc.status_code, exc.detail)
        run.status = "FAILED"; run.error = str(exc.detail); run.completed_at = datetime.now(UTC); db.commit()
        raise
    except Exception as exc:
        logger.exception("Search run %s failed unexpectedly", run.id)
        run.status = "FAILED"; run.error = str(exc)[:2000]; run.completed_at = datetime.now(UTC); db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Job search failed") from exc


@router.get("", response_model=list[JobRead])
def list_jobs(user: CurrentUser, db: DbSession, status_filter: str | None = None) -> list[JobRead]:
    query = db.query(ScrapedJob).filter(ScrapedJob.user_id == user.id)
    if status_filter:
        query = query.filter(ScrapedJob.ai_status == status_filter.upper())
    return [to_read(job) for job in query.order_by(ScrapedJob.match_score.desc().nullslast(), ScrapedJob.scraped_at.desc()).all()]


@router.get("/runs")
def list_runs(user: CurrentUser, db: DbSession) -> list[dict]:
    runs = db.query(SearchRun).filter(SearchRun.user_id == user.id).order_by(SearchRun.created_at.desc()).limit(20).all()
    return [{"id": run.id, "status": run.status, "fetched_count": run.fetched_count, "accepted_count": run.accepted_count, "rejected_count": run.rejected_count, "error": run.error, "created_at": run.created_at} for run in runs]
