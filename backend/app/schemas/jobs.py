from pydantic import BaseModel, Field


class JobScrapeRequest(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=4, examples=[["linkedin"]])
    search_term: str = Field(min_length=2, max_length=200)
    location: str = Field(default="India", min_length=2, max_length=200)
    job_type: str | None = Field(default=None, max_length=50, description="e.g. fulltime, parttime, internship")
    # None means no remote filter. This is important for Indeed, which cannot
    # reliably combine the remote and date-posted filters.
    is_remote: bool | None = None
    hours_old: int = Field(default=168, ge=1, le=720)
    results_wanted: int = Field(default=20, ge=1, le=100)
    indeed_country: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Required for Indeed searches, e.g. India, USA, or UK",
    )
    experience: str = Field(min_length=1, max_length=100)
    min_salary_lpa: int | None = Field(default=None, ge=0, le=1000)
    target_relevant_count: int = Field(default=10, ge=1, le=50)


class JobRead(BaseModel):
    id: int | None = None
    source: str
    external_url: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    date_posted: str | None = None
    ai_status: str = "PENDING"
    match_score: float | None = None
    ai_reason: str | None = None
    experience_match: str | None = None


class JobScrapeResponse(BaseModel):
    run_id: int
    accepted_jobs: list[JobRead]
    rejected_jobs: list[JobRead]
    fetched_count: int
    saved_count: int
