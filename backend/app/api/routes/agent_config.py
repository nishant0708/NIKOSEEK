from fastapi import APIRouter

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.schemas.agent_config import AgentConfigRead, AgentConfigUpdate, SystemPromptUpdate
from app.services.agent_config import (
    get_match_scoring_model, get_match_scoring_prompt, save_match_scoring_prompt, set_match_scoring_model,
)

router = APIRouter(prefix="/agent-config", tags=["agent configuration"])


@router.get("", response_model=AgentConfigRead)
def get_agent_config(user: CurrentUser, db: DbSession) -> AgentConfigRead:
    return AgentConfigRead(
        match_scoring={"model": get_match_scoring_model(user)},
        system_prompt=get_match_scoring_prompt(db),
        can_edit_system_prompt=user.is_admin,
    )


@router.put("", response_model=AgentConfigRead)
def update_agent_config(payload: AgentConfigUpdate, user: CurrentUser, db: DbSession) -> AgentConfigRead:
    set_match_scoring_model(user, payload.match_scoring.model)
    db.commit()
    return get_agent_config(user, db)


@router.put("/system-prompt", response_model=AgentConfigRead)
def update_system_prompt(payload: SystemPromptUpdate, admin: CurrentAdmin, db: DbSession) -> AgentConfigRead:
    save_match_scoring_prompt(db, payload.content, admin)
    db.commit()
    return get_agent_config(admin, db)
