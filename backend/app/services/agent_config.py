from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.security import decrypt_secret, encrypt_secret, hash_password
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


def _linkedin_config(user: User) -> dict:
    config = user.agent_config or {}
    linkedin = config.get("linkedin")
    return dict(linkedin) if isinstance(linkedin, dict) else {}


def linkedin_public_payload(user: User) -> dict[str, str | bool]:
    linkedin = _linkedin_config(user)
    email = linkedin.get("email")
    return {
        "email": email.strip() if isinstance(email, str) else "",
        "password_set": bool(linkedin.get("password_hash") or linkedin.get("password_encrypted")),
    }


def set_linkedin_credentials(user: User, email: str, password: str | None) -> None:
    config = dict(user.agent_config or {})
    linkedin = _linkedin_config(user)
    linkedin["email"] = email.strip()
    if password:
        linkedin["password_hash"] = hash_password(password)
        linkedin["password_encrypted"] = encrypt_secret(password)
    config["linkedin"] = linkedin
    user.agent_config = config
    flag_modified(user, "agent_config")


def get_linkedin_login(user: User) -> tuple[str, str]:
    linkedin = _linkedin_config(user)
    email = linkedin.get("email")
    encrypted = linkedin.get("password_encrypted")
    if not isinstance(email, str) or not email.strip():
        raise ValueError("Add your LinkedIn email on the Profile page before applying.")
    if not isinstance(encrypted, str) or not encrypted:
        raise ValueError("Add your LinkedIn password on the Profile page before applying.")
    return email.strip(), decrypt_secret(encrypted)
