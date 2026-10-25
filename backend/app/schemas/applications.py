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
    resume_agent: dict = Field(default_factory=dict)
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
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_total_tokens: int = 0
    llm_calls: int = 0
    pending_question: dict | None = None
    job: ApplicationJobRead


class ApplicationQueueRequest(BaseModel):
    job_ids: list[int] = Field(min_length=1)
    resume_key: str | None = Field(default=None, max_length=1000)


class ApplicationFromUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class ApplicationResumeSwitch(BaseModel):
    resume_key: str = Field(min_length=3, max_length=1000)


class ApplicationQueueResponse(BaseModel):
    message: str
    applications: list[ApplicationRead]


class ApplicationFieldValues(BaseModel):
    values: dict[str, str] = Field(min_length=1)
    remember: bool = True


class ApplicationStatusUpdate(BaseModel):
    status: str = Field(pattern="^(FAILED|COMPLETED|REJECTED)$")
    reason: str | None = Field(default=None, max_length=500)


class ApplicationAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=40)
    option: int | None = None
    instruction: str | None = Field(default=None, max_length=1000)
    done: bool = False


class ApplicationInstruction(BaseModel):
    instruction: str = Field(min_length=2, max_length=1000)


class BrowserInput(BaseModel):
    kind: str = Field(pattern="^(click|type|key|scroll|back|reload|front)$")
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    text: str | None = Field(default=None, max_length=500)
    key: str | None = Field(default=None, max_length=20)
    dy: float | None = None
