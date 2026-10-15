from pydantic import BaseModel, Field


class MatchScoringConfig(BaseModel):
    model: str = Field(min_length=1, max_length=120)


class AgentConfigRead(BaseModel):
    match_scoring: MatchScoringConfig
    system_prompt: str
    can_edit_system_prompt: bool


class AgentConfigUpdate(BaseModel):
    match_scoring: MatchScoringConfig


class SystemPromptUpdate(BaseModel):
    content: str = Field(min_length=20, max_length=20000)


class AdminStatusUpdate(BaseModel):
    is_admin: bool


class AgentPromptUpdate(BaseModel):
    content: str = Field(min_length=20, max_length=20000)


class ResumeAgentSettings(BaseModel):
    enabled: bool = False
    reuse_threshold: int = Field(default=75, ge=1, le=100)
    tailor_min: int = Field(default=55, ge=0, le=99)
    max_rounds: int = Field(default=2, ge=1, le=3)
    model: str = Field(default="", max_length=120)


class AutoSubmitSettings(BaseModel):
    enabled: bool = False


class CronAgentSettings(BaseModel):
    """Saved settings for the ambient fetch-and-apply agent."""

    enabled: bool = False
    role: str = Field(default="", max_length=200)
    sources: list[str] = Field(default_factory=lambda: ["linkedin"])
    jobs_per_run: int = Field(default=10, ge=1, le=50)
    hours_old: int = Field(default=24, ge=1, le=720)
    interval_hours: int = Field(default=6, ge=1, le=168)
    scheduled_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=100)
    location: str = Field(default="India", min_length=2, max_length=200)
    indeed_country: str | None = Field(default="India", max_length=100)
    experience: str = Field(default="0-1 years", min_length=1, max_length=100)
    last_run_at: str | None = None
    last_error: str | None = None
