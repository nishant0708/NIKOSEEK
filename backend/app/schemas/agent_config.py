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
