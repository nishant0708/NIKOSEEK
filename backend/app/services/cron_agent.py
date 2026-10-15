"""Ambient scheduler for finding and applying to newly posted, relevant jobs.

The scheduler deliberately reuses the normal search and application queue. That
keeps scoring, provider-login checks and the human-review handoff identical to
the manual workflow. In particular, a form that needs review stays open in its
browser tab; the scheduler only queues the next application.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.api.routes.jobs import scrape_jobs
from app.db.models import ScrapedJob, User
from app.db.session import SessionLocal
from app.schemas.jobs import JobScrapeRequest
from app.services.agent_config import cron_agent_settings, record_cron_agent_run
from app.services.applications import application_connection_error, enqueue_applications

logger = logging.getLogger(__name__)


def _is_due(settings: dict, now: datetime) -> bool:
    """Run on the configured local-time grid (e.g. 03:00, 09:00, 15:00)."""
    try:
        zone = ZoneInfo(str(settings.get("timezone") or "Asia/Kolkata"))
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("Asia/Kolkata")
    local_now = now.astimezone(zone)
    hour, minute = (int(part) for part in str(settings["scheduled_time"]).split(":", 1))
    today_anchor = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    last = settings.get("last_run_at")
    if not isinstance(last, str) or not last:
        # A newly enabled agent waits until its selected start time rather than
        # unexpectedly searching as soon as the settings are saved.
        if local_now < today_anchor:
            return False
        anchor = today_anchor
        parsed = datetime.min.replace(tzinfo=UTC)
    else:
        anchor = today_anchor if local_now >= today_anchor else today_anchor - timedelta(days=1)
        try:
            parsed = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        except ValueError:
            parsed = datetime.min.replace(tzinfo=UTC)
    interval = timedelta(hours=int(settings["interval_hours"]))
    latest_slot = anchor + interval * int((local_now - anchor) // interval)
    return parsed < latest_slot.astimezone(UTC)


def run_due_cron_agents() -> list[tuple[int, int]]:
    """Run due configurations in a worker thread and return newly queued apps."""
    db = SessionLocal()
    queued: list[tuple[int, int]] = []
    try:
        now = datetime.now(UTC)
        users = db.query(User).all()
        for user in users:
            config = cron_agent_settings(user)
            if not config["enabled"] or not config["role"].strip() or not _is_due(config, now):
                continue
            try:
                scrape_jobs(JobScrapeRequest(
                    sources=config["sources"], search_term=config["role"], location=config["location"],
                    country_indeed=config["indeed_country"] if "indeed" in config["sources"] else None,
                    experience=config["experience"], hours_old=config["hours_old"],
                    results_wanted=min(100, max(30, config["jobs_per_run"] * 10)),
                    target_relevant_count=config["jobs_per_run"], fetch_mode="cron",
                ), user, db)
                # Include a previous scheduled match that was held because its
                # provider was disconnected. It should apply on the first run
                # after the user reconnects, not be stranded as a duplicate.
                job_ids = [job_id for (job_id,) in db.query(ScrapedJob.id).filter(
                    ScrapedJob.user_id == user.id,
                    ScrapedJob.fetched_by_cron.is_(True),
                    ScrapedJob.ai_status == "ACCEPTED",
                    ScrapedJob.application_status == "PENDING",
                ).all()]
                connection_error = application_connection_error(db, user, job_ids) if job_ids else None
                if connection_error:
                    error = f"Fetched matches, but automatic apply is waiting for {connection_error.replace('_', ' ').lower()}."
                elif job_ids:
                    applications = enqueue_applications(db, user, job_ids, None)
                    queued.extend((user.id, application.id) for application in applications)
                    error = None
                else:
                    error = None
                record_cron_agent_run(user, ran_at=now.isoformat(), error=error)
                db.commit()
                logger.info("Cron agent finished for user_id=%s: accepted=%s queued=%s", user.id, len(job_ids), len(queued))
            except Exception as exc:
                db.rollback()
                # Save a short, visible error but leave the setting enabled for the next interval.
                try:
                    record_cron_agent_run(user, ran_at=now.isoformat(), error=str(exc)[:500])
                    db.commit()
                except Exception:
                    db.rollback()
                logger.exception("Cron agent failed for user_id=%s", user.id)
    finally:
        db.close()
    return queued


async def cron_agent_worker(queue: asyncio.Queue[tuple[int, int]]) -> None:
    """Check on a short cadence; each user controls their own longer interval."""
    while True:
        try:
            queued = await asyncio.to_thread(run_due_cron_agents)
            for item in queued:
                queue.put_nowait(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cron-agent scheduler check failed")
        await asyncio.sleep(60)
