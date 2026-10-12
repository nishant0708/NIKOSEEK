from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.schemas.agent_config import AgentConfigRead, AgentConfigUpdate, AgentPromptUpdate, SystemPromptUpdate
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


# ---- editable agent prompts (navigation, page check, form filling) ----------
from app.services.prompts import PROMPTS, list_prompts, missing_variables, set_prompt  # noqa: E402


@router.get("/prompts")
def get_agent_prompts(user: CurrentUser) -> list[dict]:
    return list_prompts(user)


@router.put("/prompts/{key}")
def update_agent_prompt(key: str, payload: AgentPromptUpdate, user: CurrentUser, db: DbSession) -> list[dict]:
    if key not in PROMPTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown prompt")
    missing = missing_variables(key, payload.content)
    if missing:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Prompt is missing placeholders: {', '.join('{{' + m + '}}' for m in missing)}")
    set_prompt(user, key, payload.content)
    db.commit()
    db.refresh(user)
    return list_prompts(user)


@router.delete("/prompts/{key}")
def reset_agent_prompt(key: str, user: CurrentUser, db: DbSession) -> list[dict]:
    if key not in PROMPTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown prompt")
    set_prompt(user, key, None)
    db.commit()
    db.refresh(user)
    return list_prompts(user)


# ---- resume agent (Workflow page toggle node + settings) ---------------------
from app.schemas.agent_config import ResumeAgentSettings  # noqa: E402
from app.services.resume_agent import resume_agent_settings, set_resume_agent_settings  # noqa: E402


@router.get("/resume-agent", response_model=ResumeAgentSettings)
def get_resume_agent(user: CurrentUser) -> ResumeAgentSettings:
    return ResumeAgentSettings(**resume_agent_settings(user))


@router.put("/resume-agent", response_model=ResumeAgentSettings)
def update_resume_agent(payload: ResumeAgentSettings, user: CurrentUser, db: DbSession) -> ResumeAgentSettings:
    try:
        saved = set_resume_agent_settings(user, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return ResumeAgentSettings(**saved)
