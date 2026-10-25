"""Jobs you found yourself: paste a LinkedIn or Indeed posting URL and it goes straight to the apply queue.

Match scoring is skipped (you already chose the job). Title, company and location are read from the
posting when the apply agent opens it; the resume agent reads the job description the same way.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.db.models import ScrapedJob, User
from app.services.job_navigator import CURRENT_JOB_ID_PATTERN, JOB_ID_PATTERN

LINKEDIN = "linkedin"
INDEED = "indeed"
MANUAL = "MANUAL"  # ai_status of a job you added by URL (never scored)
MANUAL_REASON = "Added by you from a job URL. AI match scoring was skipped."

INDEED_KEY_PATTERN = re.compile(r"^[0-9a-f]{12,20}$", re.I)
FETCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
logger = logging.getLogger(__name__)


class JobUrlError(ValueError):
    pass


def parse_job_url(url: str) -> tuple[str, str, str]:
    """(source, canonical URL, job key) for a LinkedIn or Indeed posting URL."""
    raw = (url or "").strip()
    if not raw:
        raise JobUrlError("Paste a LinkedIn or Indeed job URL")
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()

    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        match = JOB_ID_PATTERN.search(raw) or CURRENT_JOB_ID_PATTERN.search(raw)
        if not match:
            raise JobUrlError("That LinkedIn link isn't a job posting. Open the job and copy its URL (it contains /jobs/view/).")
        job_id = match.group(1)
        return LINKEDIN, f"https://www.linkedin.com/jobs/view/{job_id}/", job_id

    if host == "indeed.com" or host.endswith(".indeed.com"):
        query = parse_qs(parsed.query)
        key = (query.get("jk") or query.get("vjk") or [""])[0]
        if not INDEED_KEY_PATTERN.match(key):
            raise JobUrlError("That Indeed link isn't a job posting. Open the job and copy its URL (it contains jk=).")
        base = host if host != "indeed.com" else "www.indeed.com"
        return INDEED, f"https://{base}/viewjob?jk={key.lower()}", key.lower()

    raise JobUrlError("Only LinkedIn and Indeed job URLs are supported for now")


def find_existing_job(db: Session, user: User, source: str, key: str) -> ScrapedJob | None:
    """A job you already have for this posting (scraped or added before), whatever URL form it was saved with."""
    needle = f"%{key}%" if source == LINKEDIN else f"%jk={key}%"
    return (
        db.query(ScrapedJob)
        .filter(ScrapedJob.user_id == user.id, ScrapedJob.source.ilike(source), ScrapedJob.external_url.ilike(needle))
        .order_by(ScrapedJob.id.asc())
        .first()
    )


def create_manual_job(db: Session, user: User, source: str, url: str) -> ScrapedJob:
    job = ScrapedJob(user_id=user.id, source=source, external_url=url, ai_status=MANUAL, ai_reason=MANUAL_REASON)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


TITLE_SELECTORS = (
    ".job-details-jobs-unified-top-card__job-title h1", ".jobs-unified-top-card__job-title", "h1.t-24",  # LinkedIn
    '[data-testid="jobsearch-JobInfoHeader-title"]', ".jobsearch-JobInfoHeader-title",                   # Indeed
    "h1",
)
COMPANY_SELECTORS = (
    ".job-details-jobs-unified-top-card__company-name a", ".job-details-jobs-unified-top-card__company-name",
    ".jobs-unified-top-card__company-name", ".topcard__org-name-link",                                    # LinkedIn
    '[data-testid="inlineHeader-companyName"]', '[data-company-name="true"]',                            # Indeed
)
LOCATION_SELECTORS = (
    ".job-details-jobs-unified-top-card__primary-description-container span.tvm__text",
    ".topcard__flavor--bullet",                                                                          # LinkedIn
    '[data-testid="inlineHeader-companyLocation"]', '[data-testid="job-location"]',                     # Indeed
)


async def _first_text(page, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                text = " ".join((await locator.inner_text(timeout=2500)).split())
                if text:
                    return text[:300]
        except Exception:
            continue
    return None


def _from_document_title(title: str) -> tuple[str | None, str | None]:
    """'(3) Backend Developer | Acme | LinkedIn', 'Acme hiring Backend Developer in Pune | LinkedIn'
    or 'Backend Developer - Acme - Pune | Indeed.com' -> (title, company)."""
    title = re.sub(r"^\(\d+\)\s*", "", title or "").strip()
    title = re.sub(r"\s*\|\s*(LinkedIn( Jobs)?|Indeed(\.com)?)\s*$", "", title, flags=re.I)
    hiring = re.match(r"^(.+?) hiring (.+?)(?: in .+)?$", title)
    if hiring:
        return hiring.group(2).strip(), hiring.group(1).strip()
    at = re.match(r"^(.+) at (.+?)(?:\s+[\u2014-]\s+.+)?$", title)  # 'Full Stack Python - Associate at PwC — Bengaluru'
    if at:
        return at.group(1).strip(), at.group(2).strip()
    parts = [p.strip() for p in re.split(r"\s+\|\s+|\s+-\s+", title) if p.strip()]
    return (parts[0] if parts else None), (parts[1] if len(parts) > 1 else None)


async def fill_job_basics(page, job: ScrapedJob) -> bool:
    """Fill a URL-added job's title / company / location from the open posting. True when anything changed."""
    title = await _first_text(page, TITLE_SELECTORS)
    company = await _first_text(page, COMPANY_SELECTORS)
    location = await _first_text(page, LOCATION_SELECTORS)
    if not title or not company:
        try:
            doc_title, doc_company = _from_document_title(await page.title())
        except Exception:
            doc_title, doc_company = None, None
        title, company = title or doc_title, company or doc_company
    changed = False
    for field, value in (("title", title), ("company", company), ("location", location)):
        if value and not getattr(job, field):
            setattr(job, field, value[:500])
            changed = True
    return changed


def _text(soup: BeautifulSoup, selector: str) -> str | None:
    node = soup.select_one(selector)
    text = " ".join(node.get_text(" ").split()) if node else ""
    return text or None


INDEED_JOB_QUERY = """query { jobData(input: {jobKeys: [%s]}) { results { job {
  title employer { name } location { formatted { long } } description { html } } } } }"""


def _indeed_details(job: ScrapedJob) -> dict[str, str | None] | None:
    """Indeed's web pages sit behind a bot check, so ask its mobile API (the one JobSpy searches) for this job key."""
    from jobspy.indeed.constant import api_headers

    parsed = urlparse(job.external_url)
    key = (parse_qs(parsed.query).get("jk") or [""])[0]
    if not INDEED_KEY_PATTERN.match(key):
        return None
    sub = (parsed.hostname or "").split(".")[0].lower()
    country = {"www": "US", "uk": "GB", "": "US"}.get(sub, sub.upper())
    response = httpx.post("https://apis.indeed.com/graphql", headers={**api_headers, "indeed-co": country},
                          json={"query": INDEED_JOB_QUERY % json.dumps(key)}, timeout=8)
    results = (((response.json() or {}).get("data") or {}).get("jobData") or {}).get("results") or []
    data = (results[0] or {}).get("job") if results else None
    if not data:
        return None
    html = ((data.get("description") or {}).get("html")) or ""
    return {
        "title": data.get("title"),
        "company": (data.get("employer") or {}).get("name"),
        "location": ((data.get("location") or {}).get("formatted") or {}).get("long"),
        "description": " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split()) or None,
    }


def _linkedin_details(job: ScrapedJob) -> dict[str, str | None] | None:
    """LinkedIn serves its public (guest) job page to plain HTTP."""
    response = httpx.get(job.external_url, headers=FETCH_HEADERS, timeout=8, follow_redirects=True)
    if response.status_code != 200:
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    title = _text(soup, "h1.top-card-layout__title") or _text(soup, "h1")
    company = _text(soup, "a.topcard__org-name-link") or _text(soup, ".topcard__org-name-link")
    if not title or not company:
        doc_title, doc_company = _from_document_title(soup.title.get_text() if soup.title else "")
        title, company = title or doc_title, company or doc_company
    return {
        "title": title, "company": company, "location": _text(soup, ".topcard__flavor--bullet"),
        "description": _text(soup, ".show-more-less-html__markup") or _text(soup, ".description__text"),
    }


def prefetch_job_details(job: ScrapedJob) -> bool:
    """Read title / company / location / description right when you paste the URL, so the queue shows the
    real job instead of a placeholder. If this fails, the apply agent reads them when it opens the posting.
    True when anything changed. Never raises."""
    try:
        details = _indeed_details(job) if job.source.lower() == INDEED else _linkedin_details(job)
    except Exception as exc:
        logger.info("Could not prefetch %s: %s", job.external_url, exc)
        return False
    if not details:
        return False
    changed = False
    for field, limit in (("title", 500), ("company", 500), ("location", 500), ("description", 12000)):
        value = details.get(field)
        if value and not getattr(job, field):
            setattr(job, field, value[:limit])
            changed = True
    return changed

