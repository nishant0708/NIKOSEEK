"""Structured applicant profile for long company forms (Workday, Greenhouse, iCIMS, ...).

Big ATS forms ask for first/last name, address, phone country code, every job and degree with
month/year dates. Free-text profile.md + details.md is turned into JSON once by the LLM
(prompt "profile_structuring", editable), stored next to them in Supabase as Profile/structured.json,
and rebuilt automatically when either file (or the prompt) changes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date

from app.db.models import User
from app.services.llm import generate_json_object
from app.services.prompts import get_template, render
from app.services.storage import read_details_markdown, read_profile_markdown, read_structured_profile, save_structured_profile

logger = logging.getLogger(__name__)

_memory: dict[str, tuple[str, dict]] = {}


def _source(username: str) -> str:
    return f"{read_profile_markdown(username) or ''}\n\n{read_details_markdown(username) or ''}"


async def structured_profile(user: User) -> dict:
    """The structured profile (cached by content hash). {} when there is no profile or the LLM fails."""
    source = await asyncio.to_thread(_source, user.username)
    if not source.strip():
        return {}
    digest = hashlib.sha256((source + get_template(user, "profile_structuring")).encode("utf-8")).hexdigest()[:20]
    cached = _memory.get(user.username)
    if cached and cached[0] == digest:
        return cached[1]
    stored = await asyncio.to_thread(read_structured_profile, user.username)
    if stored and stored.get("_source_hash") == digest:
        data = stored.get("profile") or {}
        _memory[user.username] = (digest, data)
        return data
    try:
        data = await generate_json_object(render(user, "profile_structuring", profile=source, today=date.today().isoformat()))
    except Exception as exc:
        logger.warning("Could not build the structured profile: %s", exc)
        return (stored or {}).get("profile") or {}
    data = {k: v for k, v in data.items() if v not in ("", None, [], {})}
    _memory[user.username] = (digest, data)
    try:
        await asyncio.to_thread(save_structured_profile, user.username, {"_source_hash": digest, "profile": data})
    except Exception as exc:
        logger.warning("Could not save the structured profile: %s", exc)
    return data


def structured_block(data: dict) -> str:
    if not data:
        return ""
    return ("\n\n## Structured profile (use for name parts, address, phone code, and the Nth work-experience / education entry)\n"
            + json.dumps(data, ensure_ascii=False, indent=1))
