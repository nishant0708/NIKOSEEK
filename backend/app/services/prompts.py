"""Editable agent prompts (Config workflow page).

Each prompt has a built-in default. A user can override it; the override is stored in
users.agent_config["prompts"][<key>]. Placeholders use {{name}} so JSON examples with
single braces stay literal.
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm.attributes import flag_modified

from app.db.models import User

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

PROMPTS: dict[str, dict] = {
    "job_navigation": {
        "title": "Job navigation",
        "description": "Picks which button/link to click to get from the LinkedIn posting to the company's application form.",
        "variables": ["job_title", "job_company", "url", "page_title", "body_preview", "elements", "history"],
        "output": '{"action":"click","idx":<number>,"reason":"..."} or {"action":"ask_user","reason":"..."}',
        "template": """You are a browser agent. Goal: reach the EXTERNAL company job application FORM
(name / email / resume fields) for the job "{{job_title}}" at "{{job_company}}". You start from a LinkedIn job posting
and the browser is already logged in to LinkedIn.

Rules:
- Easy Apply, Submit and sign-in controls were already removed from the list; never try to work around that.
- Prefer an "Apply" / "Apply on company website" button or link (often with an external-link icon).
- On a company careers page that only shows a job description, click its Apply / Apply Now button.
- Dismiss cookie banners only if they block the Apply button.
- If there is a CAPTCHA, or no sensible element, answer ask_user.

Current page:
URL: {{url}}
Title: {{page_title}}
Text preview: {{body_preview}}

Clickable elements:
{{elements}}

Previous actions (do not repeat a failed one): {{history}}

Return ONLY raw JSON: {"action":"click","idx":<number>,"reason":"..."} or {"action":"ask_user","reason":"..."}""",
    },
    "page_verification": {
        "title": "Correct-page check",
        "description": "Before filling, confirms the open tab really is the application for THIS job (guards against wrong tabs when many jobs run).",
        "variables": ["job_title", "job_company", "url", "page_title", "body_preview"],
        "output": '{"match": true|false, "reason": "..."}',
        "template": """You verify a browser tab before an agent fills a job application.
Expected job: "{{job_title}}" at company "{{job_company}}".

Tab:
URL: {{url}}
Title: {{page_title}}
Text preview: {{body_preview}}

Is this tab the job posting or application form for the expected job at the expected company?
Company career sites / ATS (Greenhouse, Lever, Workday, Ashby, ...) often show the company name only in the URL or header — that counts.
Answer false if it is clearly a different job title, a different company, a login page, or an unrelated page.
Return ONLY raw JSON: {"match": true, "reason": "..."} or {"match": false, "reason": "..."}""",
    },
    "form_mapping": {
        "title": "Form filling",
        "description": "Maps your profile onto the fields of the application form (external forms and LinkedIn Easy Apply).",
        "variables": ["job_title", "job_company", "profile", "fields"],
        "output": '{"f0": "value", "f1": "ASK_USER"}',
        "template": """You are an intelligent form filler for a job application ("{{job_title}}" at "{{job_company}}").
Applicant profile data:
---
{{profile}}
---

Input fields found on the application form. Use "label" to understand what each asks.
"options" lists the allowed choices for select / radio / combobox fields.
---
{{fields}}
---

Return ONLY a raw JSON object mapping each field "key" to the string to enter.
CRITICAL: if the profile does NOT explicitly contain the answer (e.g. notice period, CTC, visa), the value MUST be exactly "ASK_USER". Do not guess.
CRITICAL: copy values from the profile exactly, character for character; never shorten or paraphrase.
CRITICAL: for fields with "options" the value must be exactly one of the options, otherwise "ASK_USER".
CRITICAL: consent / terms / EEO / demographic questions are ALWAYS "ASK_USER".
For numeric "years of experience" questions answer with a whole number only if the profile makes it explicit.
Example: {"f0": "John", "f1": "ASK_USER"}""",
    },
}


def _overrides(user: User) -> dict:
    prompts = (user.agent_config or {}).get("prompts")
    return dict(prompts) if isinstance(prompts, dict) else {}


def get_template(user: User | None, key: str) -> str:
    if user is not None:
        custom = _overrides(user).get(key)
        if isinstance(custom, str) and custom.strip():
            return custom
    return PROMPTS[key]["template"]


def render(user: User | None, key: str, **values) -> str:
    template = get_template(user, key)

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            return match.group(0)
        value = values[name]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    return PLACEHOLDER.sub(substitute, template)


def list_prompts(user: User) -> list[dict]:
    overrides = _overrides(user)
    out = []
    for key, meta in PROMPTS.items():
        custom = overrides.get(key)
        out.append({
            "key": key,
            "title": meta["title"],
            "description": meta["description"],
            "variables": meta["variables"],
            "output": meta["output"],
            "default": meta["template"],
            "content": custom if isinstance(custom, str) and custom.strip() else meta["template"],
            "customized": bool(isinstance(custom, str) and custom.strip()),
        })
    return out


def missing_variables(key: str, content: str) -> list[str]:
    used = set(PLACEHOLDER.findall(content))
    return [name for name in PROMPTS[key]["variables"] if name not in used]


def set_prompt(user: User, key: str, content: str | None) -> None:
    config = dict(user.agent_config or {})
    prompts = _overrides(user)
    if content is None or content.strip() == PROMPTS[key]["template"].strip():
        prompts.pop(key, None)
    else:
        prompts[key] = content
    config["prompts"] = prompts
    user.agent_config = config
    flag_modified(user, "agent_config")
