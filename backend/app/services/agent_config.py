from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import SystemPrompt, User

MATCH_SCORING_PROMPT_KEY = "match_scoring"
RETIRED_MODELS = {"gemini-2.5-flash-lite"}
DEFAULT_MATCH_SCORING_PROMPT = """You are a strict job-match evaluator. Score each job from 0 to 100 for this candidate.
Candidate profile:\n{profile_markdown}\n\nAdditional details:\n{details_markdown}
Search preferences: role={search_term}; experience={experience}; minimum salary={min_salary_lpa} LPA; location={location}; work preference={work_preference}.
Accept only credible matches that align with role, seniority, skills and location. Missing salary alone must not reject a job. Mark accepted only where score is 65 or higher. Reasons must be concise and actionable."""


def get_match_scoring_model(user: User) -> str:
    config = user.agent_config or {}
    model = config.get("match_scoring", {}).get("model")
    if isinstance(model, str) and model.strip() and model not in RETIRED_MODELS:
        return model
    return settings.ai_model


def set_match_scoring_model(user: User, model: str) -> None:
    config = dict(user.agent_config or {})
    config["match_scoring"] = {"model": model.strip()}
    user.agent_config = config


def get_system_prompt(db: Session) -> SystemPrompt | None:
    return db.query(SystemPrompt).filter(SystemPrompt.key == MATCH_SCORING_PROMPT_KEY).first()


def get_match_scoring_prompt(db: Session) -> str:
    record = get_system_prompt(db)
    return record.content if record else DEFAULT_MATCH_SCORING_PROMPT


def save_match_scoring_prompt(db: Session, content: str, user: User) -> None:
    record = get_system_prompt(db)
    if record is None:
        db.add(SystemPrompt(key=MATCH_SCORING_PROMPT_KEY, content=content, updated_by_user_id=user.id))
    else:
        record.content = content
        record.updated_by_user_id = user.id
