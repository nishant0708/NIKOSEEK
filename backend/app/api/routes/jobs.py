from datetime import UTC, datetime
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.db.models import ScrapedJob, SearchRun
from app.schemas.jobs import JobRead, JobScrapeRequest, JobScrapeResponse
from app.services.agent_config import (
    get_match_scoring_model,
    get_match_scoring_prompt,
)
from app.services.job_matching import attach_analysis, score_jobs
from app.services.storage import read_details_markdown, read_profile_markdown

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)

SUPPORTED_SOURCES = {"linkedin", "indeed", "naukri"}
NAUKRI_SEARCH_URL = "https://www.naukri.com/jobapi/v3/search"
# Keep requests small enough to stop early, without sending one Gemini request
# per listing (which can trigger rate limits and produce a 502 response).
SCORING_BATCH_SIZE = 5


def row_value(row: Any, name: str) -> str | None:
    """Safely get a usable string from a pandas JobSpy result row."""
    value = row.get(name)

    if value is None or pd.isna(value):
        return None

    value_as_string = str(value).strip()
    if not value_as_string or value_as_string.lower() in {"nan", "<na>", "none"}:
        return None

    return value_as_string


def to_read(job: ScrapedJob) -> JobRead:
    return JobRead(
        id=job.id,
        source=job.source,
        external_url=job.external_url,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        date_posted=job.date_posted,
        ai_status=job.ai_status or "PENDING",
        match_score=job.match_score,
        ai_reason=job.ai_reason,
        experience_match=job.experience_match,
    )


def verify_naukri_available(payload: JobScrapeRequest) -> None:
    """Raise a useful API error when Naukri blocks automated searches.

    JobSpy currently turns Naukri's CAPTCHA response into an empty DataFrame,
    which otherwise makes a blocked search indistinguishable from no matches.
    """
    params = {
        "noOfResults": 1,
        "urlType": "search_by_keyword",
        "searchType": "adv",
        "keyword": payload.search_term,
        "k": payload.search_term,
        "pageNo": 1,
        "seoKey": f"{payload.search_term.lower().replace(' ', '-')}-jobs",
        "src": "jobsearchDesk",
        "location": payload.location,
        "days": payload.hours_old // 24,
    }
    request = Request(
        f"{NAUKRI_SEARCH_URL}?{urlencode(params)}",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.naukri.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "appid": "109",
            "systemid": "Naukri",
        },
    )

    try:
        with urlopen(request, timeout=15):
            return
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace").lower()

        if exc.code == 406 and "recaptcha" in response_body:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Naukri is requiring CAPTCHA verification and cannot be "
                    "searched automatically right now. Try LinkedIn or Indeed, "
                    "or retry later from a network Naukri does not challenge."
                ),
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Naukri search is unavailable (HTTP {exc.code}).",
        ) from exc
    except URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Naukri search could not be reached.",
        ) from exc


def scrape_source(payload: JobScrapeRequest, source: str) -> pd.DataFrame:
    """
    Scrape one site at a time.

    Indeed and LinkedIn have different supported parameters, so sending them
    in separate JobSpy calls prevents Indeed-specific options from breaking
    LinkedIn searches.
    """
    from jobspy import scrape_jobs

    kwargs: dict[str, Any] = {
        "site_name": [source],
        "search_term": payload.search_term,
        "location": payload.location,
        "results_wanted": payload.results_wanted,
        "verbose": 0,
    }

    if source == "indeed":
        if not payload.indeed_country:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "indeed_country is required when Indeed is selected. "
                    "Use an exact supported value such as 'India', 'USA', or 'UK'."
                ),
            )

        # JobSpy's Indeed scraper does not apply both filters correctly.
        if payload.hours_old is not None and payload.is_remote is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Indeed does not support hours_old and is_remote together. "
                    "Send only one of these filters for an Indeed search."
                ),
            )

        kwargs["country_indeed"] = payload.indeed_country

    if payload.hours_old is not None:
        kwargs["hours_old"] = payload.hours_old

    if payload.job_type is not None:
        kwargs["job_type"] = payload.job_type

    if payload.is_remote is not None:
        kwargs["is_remote"] = payload.is_remote

    logger.info(
        "Fetching jobs from %s: term=%r, location=%r",
        source,
        payload.search_term,
        payload.location,
    )

    if source == "naukri":
        verify_naukri_available(payload)

    return scrape_jobs(**kwargs)


@router.post("/scrape", response_model=JobScrapeResponse)
def scrape_jobs(
    payload: JobScrapeRequest,
    user: CurrentUser,
    db: DbSession,
) -> JobScrapeResponse:
    unknown_sources = set(payload.sources) - SUPPORTED_SOURCES

    if unknown_sources:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only LinkedIn, Indeed, and Naukri are available in this phase.",
        )

    if not payload.sources:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Select at least one job source.",
        )

    profile = read_profile_markdown(user.username)
    details = read_details_markdown(user.username)

    if not profile or not details:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generate and review your profile before starting a job search.",
        )

    run = SearchRun(
        user_id=user.id,
        parameters=payload.model_dump(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    logger.info(
        "Search run %s started for user_id=%s, sources=%s, term=%r",
        run.id,
        user.id,
        payload.sources,
        payload.search_term,
    )

    try:
        frames: list[pd.DataFrame] = []

        for source in payload.sources:
            dataframe = scrape_source(payload, source)

            if not dataframe.empty:
                frames.append(dataframe)

        if not frames:
            run.status = "SUCCEEDED"
            run.completed_at = datetime.now(UTC)
            db.commit()

            return JobScrapeResponse(
                run_id=run.id,
                accepted_jobs=[],
                rejected_jobs=[],
                fetched_count=0,
                saved_count=0,
            )

        dataframe = pd.concat(frames, ignore_index=True)

        # Prevent duplicate jobs when the same vacancy appears from both sites.
        if "job_url" in dataframe.columns:
            dataframe = dataframe.drop_duplicates(
                subset=["job_url"],
                keep="first",
            )

        scraped: list[dict[str, str | None]] = []

        for _, row in dataframe.iterrows():
            url = row_value(row, "job_url")

            if not url:
                continue

            scraped.append(
                {
                    "source": row_value(row, "site") or "unknown",
                    "external_url": url,
                    "title": row_value(row, "title"),
                    "company": row_value(row, "company"),
                    "location": row_value(row, "location"),
                    "description": row_value(row, "description"),
                    "date_posted": row_value(row, "date_posted"),
                }
            )

        logger.info(
            "Search run %s: JobSpy returned %s usable jobs",
            run.id,
            len(scraped),
        )

        if not scraped:
            run.status = "SUCCEEDED"
            run.completed_at = datetime.now(UTC)
            db.commit()

            return JobScrapeResponse(
                run_id=run.id,
                accepted_jobs=[],
                rejected_jobs=[],
                fetched_count=0,
                saved_count=0,
            )

        model = get_match_scoring_model(user)

        accepted: list[JobRead] = []
        rejected: list[JobRead] = []
        saved_count = 0
        usage = {"input": 0, "output": 0, "total": 0}
        scoring_prompt = get_match_scoring_prompt(db)

        for batch_start in range(0, len(scraped), SCORING_BATCH_SIZE):
            batch = scraped[batch_start : batch_start + SCORING_BATCH_SIZE]
            analyses, job_usage = score_jobs(
                batch,
                profile,
                details,
                payload,
                model,
                scoring_prompt,
            )
            for token_type in usage:
                usage[token_type] += job_usage[token_type]

            analyses_by_number = {
                item.get("job_number"): item
                for item in analyses
                if item.get("job_number") is not None
            }

            for index, raw in enumerate(batch, start=1):
                analysis = analyses_by_number.get(
                    index,
                    {
                        "accepted": False,
                        "match_score": 0,
                        "reason": "No score returned.",
                        "experience_match": "Unknown",
                    },
                )

                job = ScrapedJob(
                    user_id=user.id,
                    search_run_id=run.id,
                    **raw,
                )

                candidate = attach_analysis(to_read(job), analysis)

                job.ai_status = candidate.ai_status
                job.match_score = candidate.match_score
                job.ai_reason = candidate.ai_reason
                job.experience_match = candidate.experience_match

                # Gemini reports usage for the full batch. Split it across the
                # corresponding job records while retaining the exact total on
                # the search run below.
                job.llm_input_tokens = job_usage["input"] // len(batch)
                job.llm_output_tokens = job_usage["output"] // len(batch)
                job.llm_total_tokens = job_usage["total"] // len(batch)

                db.add(job)

                try:
                    db.commit()
                    db.refresh(job)
                    saved_count += 1

                    if job.ai_status == "ACCEPTED":
                        accepted.append(to_read(job))
                    else:
                        rejected.append(to_read(job))

                    if len(accepted) >= payload.target_relevant_count:
                        logger.info(
                            "Search run %s reached its relevant-job target (%s); "
                            "stopping LLM scoring.",
                            run.id,
                            payload.target_relevant_count,
                        )
                        break

                except IntegrityError:
                    # Usually means that this URL was already saved.
                    db.rollback()
                    logger.info(
                        "Skipping duplicate job URL for search run %s: %s",
                        run.id,
                        raw["external_url"],
                    )

            if len(accepted) >= payload.target_relevant_count:
                break

        run.status = "SUCCEEDED"
        run.fetched_count = len(scraped)
        run.accepted_count = len(accepted)
        run.rejected_count = len(rejected)
        run.completed_at = datetime.now(UTC)
        run.llm_input_tokens = usage["input"]
        run.llm_output_tokens = usage["output"]
        run.llm_total_tokens = usage["total"]

        db.commit()

        return JobScrapeResponse(
            run_id=run.id,
            accepted_jobs=accepted,
            rejected_jobs=rejected,
            fetched_count=len(scraped),
            saved_count=saved_count,
        )

    except HTTPException as exc:
        logger.exception(
            "Search run %s failed with HTTP %s: %s",
            run.id,
            exc.status_code,
            exc.detail,
        )

        run.status = "FAILED"
        run.error = str(exc.detail)
        run.completed_at = datetime.now(UTC)
        db.commit()
        raise

    except Exception as exc:
        logger.exception("Search run %s failed unexpectedly", run.id)

        run.status = "FAILED"
        run.error = str(exc)[:2000]
        run.completed_at = datetime.now(UTC)
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Job search failed.",
        ) from exc


@router.get("", response_model=list[JobRead])
def list_jobs(
    user: CurrentUser,
    db: DbSession,
    status_filter: str | None = None,
) -> list[JobRead]:
    query = db.query(ScrapedJob).filter(ScrapedJob.user_id == user.id)

    if status_filter:
        query = query.filter(
            ScrapedJob.ai_status == status_filter.upper()
        )

    jobs = (
        query.order_by(
            ScrapedJob.match_score.desc().nullslast(),
            ScrapedJob.scraped_at.desc(),
        )
        .all()
    )

    return [to_read(job) for job in jobs]


@router.get("/runs")
def list_runs(user: CurrentUser, db: DbSession) -> list[dict]:
    runs = (
        db.query(SearchRun)
        .filter(SearchRun.user_id == user.id)
        .order_by(SearchRun.created_at.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "id": run.id,
            "status": run.status,
            "fetched_count": run.fetched_count,
            "accepted_count": run.accepted_count,
            "rejected_count": run.rejected_count,
            "error": run.error,
            "created_at": run.created_at,
        }
        for run in runs
    ]
