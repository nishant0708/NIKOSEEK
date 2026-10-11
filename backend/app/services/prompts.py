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
        "output": '{"action":"click","idx":<number>,"reason":"..."} or {"action":"ask_user","reason":"...","question":"...","options":[{"label":"...","idx":<number>}]}',
        "template": """You are a browser agent. Goal: reach the EXTERNAL company job application FORM
(name / email / resume fields) for the job "{{job_title}}" at "{{job_company}}". You start from a LinkedIn job posting
and the browser is already logged in to LinkedIn.

Rules:
- Easy Apply and Submit controls (and LinkedIn / Indeed sign-in controls) were already removed from the list; never try to work around that.
- Company portals (Workday, iCIMS, ...): their sign-in / create-account pages and Workday's "Apply Manually" screen are handled
  automatically, so just pick the Apply button on the job page.
- Prefer an "Apply" / "Apply on company website" button or link (often with an external-link icon).
- On a company careers page that only shows a job description, click its Apply / Apply Now button.
- Dismiss cookie banners only if they block the Apply button.
- If a previous action has "user_instruction", follow that instruction first.
- If there is a CAPTCHA, a choice only the applicant can make, or no sensible element, answer ask_user: ask ONE short
  question and offer up to 4 options, each pointing at a clickable element idx (e.g. "Click 'Continue to application'").

Current page:
URL: {{url}}
Title: {{page_title}}
Text preview: {{body_preview}}

Clickable elements:
{{elements}}

Previous actions (do not repeat a failed one): {{history}}

Return ONLY raw JSON: {"action":"click","idx":<number>,"reason":"..."} or
{"action":"ask_user","reason":"...","question":"...","options":[{"label":"...","idx":<number>}]}""",
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
Validate every answer against the profile — use the profile, "Application answers" and the applicant's notes as the source of truth.
DERIVE answers that follow directly from those facts instead of asking:
- "Years of experience with <skill>": add up the durations of roles/projects that use the skill (from the dates; today is the
  reference for "Present"), round DOWN to a whole number; if the skill is listed but per-role dates are unclear, use total
  professional experience. Answer digits only when the field is numeric.
- Notice period: copy only an explicit notice period from the profile or applicant notes. If it is not stated, return exactly "ASK_USER"; never infer 15/30/60 days.
- CTC / salary: use the numbers the applicant stated (current vs expected — never swap them), in the unit the question asks.
- Location / country / phone code / work authorization: from the profile location and earlier answers.
CRITICAL: only if NOTHING in the profile, answers or notes supports a value (e.g. CTC never mentioned anywhere), return exactly "ASK_USER". Never invent.
CRITICAL: names, emails, phone numbers and URLs must be copied exactly, character for character.
CRITICAL: for fields with "options" the value must be exactly one of the options, otherwise "ASK_USER".
Fields with "current_value" are already filled: return "KEEP" if it is right; if it clearly contradicts the profile
(e.g. phone country code "Andorra (+376)" for an applicant in India) return the correct option instead.
Match the value to the field TYPE: if "type" is number, "inputmode" is numeric/decimal, or the "hint"/"error" asks for a
number (e.g. "Enter a decimal number larger than 0.0"), answer digits only — "10 LPA" becomes "10", "1 year" becomes "1".
Keep units consistent with the question (if it asks in lakhs, 10 LPA -> 10; if it asks the yearly amount in rupees, -> 1000000).
If a field has "error", the current value was rejected: return a corrected value that satisfies the error message.
Use the "Structured profile" JSON for split names, address parts, phone country code / national number, and dates.
Fields with a "group" like "Work Experience 2" or "Education 1" belong to the Nth entry of the structured experience /
education list (most recent first): fill Job Title / Company / Location / From (Month, Year) / To / "I currently work here" /
Role Description from THAT entry. Phone number fields that sit next to a separate country-code field get the national number only.
"How did you hear about us" style questions: pick the option for the job board the job came from (LinkedIn / Indeed) when it exists.
CRITICAL: consent / terms / EEO / demographic questions are ALWAYS "ASK_USER".
For numeric "years of experience" questions answer with a whole number only if the profile makes it explicit.
Example: {"f0": "John", "f1": "ASK_USER"}""",
    },
    "profile_structuring": {
        "title": "Profile → structured data",
        "description": "Turns your profile.md + details.md into structured data (name parts, address, phone code, every job and degree with dates). "
                       "Long company forms (Workday, Greenhouse, iCIMS ...) are filled from it. Rebuilt automatically whenever your profile changes.",
        "variables": ["profile", "today"],
        "output": '{"first_name": "...", "experience": [...], "education": [...]}',
        "template": """Convert this job applicant's profile into structured JSON for filling job application forms. Today is {{today}}.
---
{{profile}}
---
Return ONLY one raw JSON object with exactly these keys:
{"first_name":"","middle_name":"","last_name":"","full_name":"","email":"","phone_country_code":"","phone_national":"",
 "country":"","state":"","city":"","postal_code":"","address_line1":"",
 "linkedin_url":"","github_url":"","portfolio_url":"",
 "current_title":"","current_company":"","total_experience_years":0,"highest_education":"",
 "experience":[{"company":"","title":"","location":"","start_month":"","start_year":"","end_month":"","end_year":"","current":false,"description":""}],
 "education":[{"school":"","degree":"","field_of_study":"","start_year":"","end_year":"","grade":"","location":"","current":false}],
 "skills":[],"languages":[],"notice_period_days":null,"current_ctc":"","expected_ctc":"","work_authorization":"","willing_to_relocate":""}
Rules:
- Copy facts exactly (names, email, phone digits, URLs, company and school names). Never invent anything: unknown -> "" (or null / []).
- Name: the heading name split into first / middle / last.
- Phone: phone_national = the digits without country code; phone_country_code like "+91" only if stated or the number is clearly
  from that country (a 10-digit Indian mobile starting 6-9 -> "+91").
- Location: city = the FIRST city of the Location line; country from it; state = that city's state/province (a well-known fact), else "".
- URLs: add "https://" when the profile omits it.
- experience / education: every entry, most recent first. Months as two digits ("06"), years as four digits; "Present" -> current=true and
  empty end dates. description = 2-4 short sentences from that role's bullets.
- total_experience_years: full-time + internship months added up until today, as a number with one decimal.
- notice_period_days / current_ctc / expected_ctc / work_authorization / willing_to_relocate: ONLY if explicitly stated, else empty.""",
    },
    "browser_assistant": {
        "title": "Browser assistant (your instructions)",
        "description": "Runs when you type an instruction for the agent (e.g. \"click Continue\", \"my phone country is India, fix it\") while a form is open or the agent is stuck. It can also go Back to an earlier step to fix it.",
        "variables": ["job_title", "job_company", "instruction", "url", "page_text", "elements", "fields", "previous_answers", "done_so_far"],
        "output": '{"reply":"...","done":true|false,"actions":[{"type":"click","idx":n} | {"type":"fill","key":"f1","value":"..."} | {"type":"back"} | {"type":"next"}]}',
        "template": """You control a browser tab for a job application ("{{job_title}}" at "{{job_company}}").
The applicant gave you this instruction:
"{{instruction}}"

What you already did for this instruction: {{done_so_far}}

Answers already entered on earlier steps of this application (step, question, answer):
{{previous_answers}}

CURRENT step — tab URL: {{url}}
Visible text: {{page_text}}

Clickable elements on the current step (idx = id to click):
{{elements}}

Form fields on the current step (key = id to fill; use one of "options" when present; obey "type"/"hint"/"error"):
{{fields}}

Rules:
- Do only what the instruction asks, with the fewest actions. Numbers-only fields get digits only (10 LPA -> 10).
- If the instruction concerns a question on an EARLIER step (see previous answers) — e.g. the applicant spotted a wrong answer on the
  review page — go there: click that section's "Edit" button if the review page has one, otherwise return {"type":"back"} (one step per
  call). Set done=false while you are still on the way; you are called again on the new step after every click / back.
- Once the right field is fixed, return done=true and STOP: do NOT press next — the app itself moves forward through the remaining
  steps (re-checking them) back to the review page.
- Never click a Submit / Send application button — the applicant submits with the Submit button in the app.
- If nothing more is needed (or it cannot be done), return no actions, done=true, and explain in "reply".
Return ONLY raw JSON: {"reply":"one short sentence","done":true,"actions":[{"type":"fill","key":"f2","value":"India (+91)"}]}""",
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
    from app.services.agent_config import fresh_config

    config = fresh_config(user)
    prompts = dict(config.get("prompts")) if isinstance(config.get("prompts"), dict) else {}
    if content is None or content.strip() == PROMPTS[key]["template"].strip():
        prompts.pop(key, None)
    else:
        prompts[key] = content
    config["prompts"] = prompts
    user.agent_config = config
    flag_modified(user, "agent_config")
