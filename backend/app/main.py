import asyncio
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import agent_config, applications, auth, indeed, jobs, linkedin, profile, resumes
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.session import SessionLocal, engine

configure_logging()
logger = logging.getLogger(__name__)
app = FastAPI(title="Job Automation Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(resumes.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(applications.router, prefix="/api/v1")
app.include_router(agent_config.router, prefix="/api/v1")
app.include_router(linkedin.router, prefix="/api/v1")
app.include_router(indeed.router, prefix="/api/v1")


@app.middleware("http")
async def log_request(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request failure: %s %s", request.method, request.url.path)
        raise
    logger.info("Request completed: %s %s -> %s in %.0fms", request.method, request.url.path, response.status_code, (time.perf_counter() - started) * 1000)
    return response


@app.on_event("startup")
async def startup() -> None:
    # Database is Supabase Postgres. The private storage bucket and policies
    # are provisioned with supabase/setup.sql.
    Base.metadata.create_all(bind=engine)
    # Synchronous endpoints (JobSpy runs in FastAPI's threadpool) use this
    # loop to publish their genuine progress over the websocket.
    app.state.event_loop = asyncio.get_running_loop()
    app.state.application_queue = asyncio.Queue()
    app.state.application_worker = asyncio.create_task(application_worker())
    from app.services.cron_agent import cron_agent_worker
    app.state.cron_agent_worker = asyncio.create_task(cron_agent_worker(app.state.application_queue))
    logger.info("Backend started with event loop %s; logs are written to backend/logs/backend.log", type(asyncio.get_running_loop()).__name__)


@app.on_event("shutdown")
async def shutdown() -> None:
    worker = getattr(app.state, "application_worker", None)
    if worker is not None:
        worker.cancel()
    cron_worker = getattr(app.state, "cron_agent_worker", None)
    if cron_worker is not None:
        cron_worker.cancel()
    from app.services.linkedin_browser import linkedin_browser
    from app.services.indeed_browser import indeed_browser

    await linkedin_browser.shutdown()
    await indeed_browser.shutdown()


async def application_worker() -> None:
    while True:
        user_id, application_id, delay_seconds = await app.state.application_queue.get()
        try:
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            from app.services.applications import process_application

            # Own task per application so "Move to failed" can cancel a stuck run without killing the worker.
            task = asyncio.create_task(process_application(application_id, SessionLocal, user_id))
            await asyncio.wait({task})
        except Exception:
            logger.exception("Application worker crashed on application %s", application_id)
        finally:
            app.state.application_queue.task_done()



@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Job Automation Platform backend is running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
