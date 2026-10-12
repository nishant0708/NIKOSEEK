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
