from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.attributes import flag_modified
import re

from app.core.config import settings
from app.core.security import decrypt_secret, encrypt_secret, hash_password
from app.db.models import SystemPrompt, User

MATCH_SCORING_PROMPT_KEY = "match_scoring"
RETIRED_MODELS = {"gemini-2.5-flash-lite"}
DEFAULT_MATCH_SCORING_PROMPT = """You are a strict job-match evaluator. Score each job from 0 to 100 for this candidate.
Candidate profile:\n{profile_markdown}\n\nAdditional details:\n{details_markdown}
Search preferences: role={search_term}; experience={experience}; minimum salary={min_salary_lpa} LPA; location={location}; work preference={work_preference}.
Accept only credible matches that align with role, seniority, skills and location. Missing salary alone must not reject a job. Mark accepted only where score is 65 or higher. Reasons must be concise and actionable."""


def fresh_config(user: User) -> dict:
    """Re-read users.agent_config with a row lock before changing it.

    Several things write this one JSON column (LinkedIn creds, Indeed creds, prompts, model, session status).
    Each request used to copy the dict it loaded earlier and write the whole thing back, so two overlapping
    writes (e.g. connecting Indeed while the LinkedIn window was being connected/saved) silently dropped the
    other provider's keys. Locking + re-reading makes every write a merge on top of the latest value.
    """
    db = object_session(user)
    if db is not None and user.id is not None:
        db.refresh(user, attribute_names=["agent_config"], with_for_update=True)
    return dict(user.agent_config or {})


def save_config(user: User, config: dict) -> None:
    user.agent_config = config
    flag_modified(user, "agent_config")


def get_match_scoring_model(user: User) -> str:
    config = user.agent_config or {}
    model = config.get("match_scoring", {}).get("model")
    if isinstance(model, str) and model.strip() and model not in RETIRED_MODELS:
        return model
    return settings.ai_model


def set_match_scoring_model(user: User, model: str) -> None:
    config = fresh_config(user)
    config["match_scoring"] = {"model": model.strip()}
    save_config(user, config)


def cron_agent_settings(user: User) -> dict:
    """Return a defensive, backwards-compatible scheduler configuration."""
    raw = (user.agent_config or {}).get("cron_agent")
    saved = dict(raw) if isinstance(raw, dict) else {}
    sources = [source for source in saved.get("sources", ["linkedin"]) if source in {"linkedin", "indeed"}]
    def bounded_int(key: str, default: int, low: int, high: int) -> int:
        try:
            return min(high, max(low, int(saved.get(key, default))))
        except (TypeError, ValueError):
            return default
    scheduled_time = str(saved.get("scheduled_time", "09:00"))
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", scheduled_time):
        scheduled_time = "09:00"
    return {
        "enabled": bool(saved.get("enabled", False)),
        "role": str(saved.get("role", ""))[:200],
        "sources": sources or ["linkedin"],
        "jobs_per_run": bounded_int("jobs_per_run", 10, 1, 50),
        "hours_old": bounded_int("hours_old", 24, 1, 720),
        "interval_hours": bounded_int("interval_hours", 6, 1, 168),
        "scheduled_time": scheduled_time,
        "timezone": str(saved.get("timezone", "Asia/Kolkata"))[:100] or "Asia/Kolkata",
        "location": str(saved.get("location", "India"))[:200] or "India",
        "indeed_country": str(saved.get("indeed_country", "India"))[:100] or "India",
        "experience": str(saved.get("experience", "0-1 years"))[:100] or "0-1 years",
        "last_run_at": saved.get("last_run_at") if isinstance(saved.get("last_run_at"), str) else None,
        "last_error": saved.get("last_error") if isinstance(saved.get("last_error"), str) else None,
    }


def set_cron_agent_settings(user: User, values: dict) -> dict:
    config = fresh_config(user)
    current = cron_agent_settings(user)
    current.update({key: value for key, value in values.items() if key not in {"last_run_at", "last_error"}})
    config["cron_agent"] = current
    save_config(user, config)
    return current


def record_cron_agent_run(user: User, *, ran_at: str, error: str | None) -> None:
    """Store scheduler metadata without treating it as an editable user setting."""
    config = fresh_config(user)
    cron = dict(config.get("cron_agent")) if isinstance(config.get("cron_agent"), dict) else {}
    cron["last_run_at"] = ran_at
    cron["last_error"] = error
    config["cron_agent"] = cron
    save_config(user, config)


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
    config = fresh_config(user)
    linkedin = dict(config.get("linkedin")) if isinstance(config.get("linkedin"), dict) else {}
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


# Keep Indeed credentials and session data in a separate namespace so neither
# provider can overwrite or read the other's login details.
def _indeed_config(user: User) -> dict:
    config = user.agent_config or {}
    indeed = config.get("indeed")
    return dict(indeed) if isinstance(indeed, dict) else {}


def indeed_public_payload(user: User) -> dict[str, str | bool]:
    indeed = _indeed_config(user)
    email = indeed.get("email")
    return {
        "email": email.strip() if isinstance(email, str) else "",
        "password_set": bool(indeed.get("password_hash") or indeed.get("password_encrypted")),
    }


def set_indeed_credentials(user: User, email: str, password: str | None) -> None:
    config = fresh_config(user)
    indeed = dict(config.get("indeed")) if isinstance(config.get("indeed"), dict) else {}
    indeed["email"] = email.strip()
    if password:
        indeed["password_hash"] = hash_password(password)
        indeed["password_encrypted"] = encrypt_secret(password)
    config["indeed"] = indeed
    user.agent_config = config
    flag_modified(user, "agent_config")


def get_indeed_login(user: User) -> tuple[str, str]:
    indeed = _indeed_config(user)
    email = indeed.get("email")
    encrypted = indeed.get("password_encrypted")
    if not isinstance(email, str) or not email.strip():
        raise ValueError("Add your Indeed email on the Profile page before applying.")
    if not isinstance(encrypted, str) or not encrypted:
        raise ValueError("Add your Indeed password on the Profile page before applying.")
    return email.strip(), decrypt_secret(encrypted)


# ---------------------------------------------------------------------------
# Company job portals (Workday, Greenhouse, iCIMS, ...): ONE email + password you chose for job-application
# accounts. The agent signs up with it the first time a company asks, then remembers that this company
# (vendor + tenant, e.g. workday: {hp: true}) already has an account and signs in next time.
#   agent_config["ats"]          = {"email", "password_encrypted", "password_hash"}
#   agent_config["ats_accounts"] = {"workday": {"hp": true, "nvidia": true}, "icims": {...}}
# ---------------------------------------------------------------------------
def _ats_config(user: User) -> dict:
    ats = (user.agent_config or {}).get("ats")
    return dict(ats) if isinstance(ats, dict) else {}


def ats_accounts(user: User) -> dict[str, dict[str, bool]]:
    accounts = (user.agent_config or {}).get("ats_accounts")
    return {v: dict(t) for v, t in accounts.items() if isinstance(t, dict)} if isinstance(accounts, dict) else {}


def ats_public_payload(user: User) -> dict:
    ats = _ats_config(user)
    email = ats.get("email")
    return {
        "email": email.strip() if isinstance(email, str) else "",
        "password_set": bool(ats.get("password_encrypted")),
        "accounts": ats_accounts(user),
    }


def set_ats_credentials(user: User, email: str, password: str | None) -> None:
    config = fresh_config(user)
    ats = dict(config.get("ats")) if isinstance(config.get("ats"), dict) else {}
    ats["email"] = email.strip()
    if password:
        ats["password_hash"] = hash_password(password)
        ats["password_encrypted"] = encrypt_secret(password)
    config["ats"] = ats
    save_config(user, config)


def get_ats_login(user: User) -> tuple[str, str] | None:
    ats = _ats_config(user)
    email, encrypted = ats.get("email"), ats.get("password_encrypted")
    if not isinstance(email, str) or not email.strip() or not isinstance(encrypted, str) or not encrypted:
        return None
    return email.strip(), decrypt_secret(encrypted)


def ats_account_known(user: User, vendor: str, tenant: str) -> bool:
    return bool(ats_accounts(user).get(vendor, {}).get(tenant))


def mark_ats_account(user: User, vendor: str, tenant: str, signed_up: bool = True) -> None:
    """Remember (and commit right away) that this company's portal already has an account for you."""
    config = fresh_config(user)
    accounts = dict(config.get("ats_accounts")) if isinstance(config.get("ats_accounts"), dict) else {}
    tenants = dict(accounts.get(vendor)) if isinstance(accounts.get(vendor), dict) else {}
    if signed_up:
        tenants[tenant] = True
    else:
        tenants.pop(tenant, None)
    accounts[vendor] = tenants
    config["ats_accounts"] = accounts
    save_config(user, config)
    db = object_session(user)
    if db is not None:
        db.commit()
