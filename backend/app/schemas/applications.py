from datetime import datetime

from pydantic import BaseModel, Field


class ApplicationJobRead(BaseModel):
    id: int
    source: str
    external_url: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    ai_status: str = "PENDING"
    match_score: float | None = None

    model_config = {"from_attributes": True}


class ApplicationRead(BaseModel):
    id: int
    job_id: int
    resume_key: str
    resume_role: str | None = None
    status: str
    stage: str
    queue_position: int
    application_url: str | None = None
    form_data: dict = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    applied_at: datetime | None = None
    completed_at: datetime | None = None
    job: ApplicationJobRead


class ApplicationQueueRequest(BaseModel):
    job_ids: list[int] = Field(min_length=1)
    resume_key: str | None = Field(default=None, max_length=1000)


class ApplicationQueueResponse(BaseModel):
    message: str
    applications: list[ApplicationRead]
