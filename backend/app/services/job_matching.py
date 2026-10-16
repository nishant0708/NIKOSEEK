import json
from collections.abc import Callable

from app.schemas.jobs import JobRead, JobScrapeRequest
from app.services.job_requirements import experience_gate
from app.services.llm import generate_json

MATCH_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {
        "job_number": {"type": "INTEGER"}, "accepted": {"type": "BOOLEAN"},
        "match_score": {"type": "NUMBER"}, "experience_match": {"type": "STRING"}, "reason": {"type": "STRING"},
    }, "required": ["job_number", "accepted", "match_score", "experience_match", "reason"]},
}

# Keep each request small enough that a search can stop before scoring every
# scraped posting. A batch may contain a few jobs beyond the final accepted
# result, but no later batches are sent to the model.
SCORING_BATCH_SIZE = 10


# Appended in code (not part of the editable prompt) so a customised prompt can't lose these rules.
JD_RULES = """

Rules for reading each job (always apply):
- Base the evaluation on the job's "description" (the real JD), not just the title.
- "experience_match" must state the JD's experience requirement next to the candidate's, e.g. "JD 3-5 yrs vs candidate 0-1 yrs".
- If the JD's minimum required experience is clearly above the candidate's experience preference, set accepted=false and score below 50.
- If a job has no description, set experience_match to "Unknown (no description)" and never assume its requirements."""

CATEGORY_INSTRUCTION = """

Resume category (scorer 2 of the resume agent): also return "category" for EVERY job — the job's PRIMARY role family,
decided from the JOB's title and responsibilities only (NOT from the candidate's profile or which resumes they have).
Choose from: {categories}. A React / TypeScript / UI role is frontend-developer even if the candidate is a backend developer;
a Python / Node API role is backend-developer; a role that clearly owns both UI and APIs is full-stack-developer.
If none of these fits, return a new short lowercase slug such as "embedded-engineer"."""
DESCRIPTION_CHARS = 5000


def _schema(with_category: bool) -> dict:
    if not with_category:
        return MATCH_SCHEMA
    item = MATCH_SCHEMA["items"]
    return {"type": "ARRAY", "items": {**item, "properties": {**item["properties"], "category": {"type": "STRING"}},
                                       "required": [*item["required"], "category"]}}


def _score_batch(jobs: list[tuple[int, dict]], profile_markdown: str, details_markdown: str, preferences: JobScrapeRequest, model: str, system_prompt: str,
                 categories: list[str] | None = None) -> tuple[list[dict], dict[str, int]]:
    payload = [{"job_number": number, **job, "description": (job.get("description") or "(not available — judge from the title only)")[:DESCRIPTION_CHARS]}
               for number, job in jobs]
    replacements = {
        "profile_markdown": profile_markdown[:12000], "details_markdown": details_markdown[:8000],
        "search_term": preferences.search_term, "experience": preferences.experience,
        "min_salary_lpa": str(preferences.min_salary_lpa or "not specified"), "location": preferences.location,
        "work_preference": "remote" if preferences.is_remote else "any",
    }
    system = system_prompt
    for key, value in replacements.items():
        system = system.replace("{" + key + "}", value)
    system += JD_RULES
    if categories:
        system += CATEGORY_INSTRUCTION.replace("{categories}", ", ".join(categories))
    result = generate_json("Evaluate these jobs:\n" + json.dumps(payload), system, _schema(bool(categories)), model=model)
    return result.data, {"input": result.input_tokens, "output": result.output_tokens, "total": result.total_tokens}


def score_jobs(jobs: list[dict], profile_markdown: str, details_markdown: str, preferences: JobScrapeRequest, model: str, system_prompt: str,
               categories: list[str] | None = None) -> tuple[list[dict], dict[str, int]]:
    return _score_batch(list(enumerate(jobs, start=1)), profile_markdown, details_markdown, preferences, model, system_prompt, categories)


def score_jobs_until_target(jobs: list[dict], profile_markdown: str, details_markdown: str, preferences: JobScrapeRequest, model: str, system_prompt: str,
                            categories: list[str] | None = None, on_batch: Callable[[str, int, int, int], None] | None = None) -> tuple[list[dict], dict[str, int]]:
    """Score ordered jobs in batches, making no further LLM calls after the target is met."""
    analyses: list[dict] = []
    usage = {"input": 0, "output": 0, "total": 0}
    accepted_count = 0

    for start in range(0, len(jobs), SCORING_BATCH_SIZE):
        batch = list(enumerate(jobs[start:start + SCORING_BATCH_SIZE], start=start + 1))
        if on_batch:
            on_batch("started", start, len(batch), accepted_count)
        batch_analyses, batch_usage = _score_batch(batch, profile_markdown, details_markdown, preferences, model, system_prompt, categories)
        for key in usage:
            usage[key] += batch_usage[key]

        by_number = {item.get("job_number"): item for item in batch_analyses}
        for number, job in batch:
            analysis = by_number.get(number, {"job_number": number, "accepted": False, "match_score": 0, "reason": "No score returned", "experience_match": "Unknown"})
            analysis = gate_experience(analysis, job, preferences.experience)
            analyses.append(analysis)
            if analysis.get("accepted"):
                accepted_count += 1
                if accepted_count >= preferences.target_relevant_count:
                    if on_batch:
                        on_batch("completed", start, len(batch), accepted_count)
                    return analyses, usage
        if on_batch:
            on_batch("completed", start, len(batch), accepted_count)

    return analyses, usage


def gate_experience(analysis: dict, job: dict, preference: str) -> dict:
    """Hard rule on top of the model: a JD that clearly asks for more experience than you searched for is rejected."""
    reason = experience_gate(job.get("description"), preference)
    if not reason:
        return analysis
    return {**analysis, "accepted": False, "match_score": min(float(analysis.get("match_score") or 0), 40.0),
            "experience_match": reason[:100], "reason": f"{reason} {analysis.get('reason') or ''}".strip()}


def attach_analysis(job: JobRead, analysis: dict) -> JobRead:
    job.ai_status = "ACCEPTED" if analysis.get("accepted") else "REJECTED"
    job.match_score = max(0, min(100, float(analysis.get("match_score", 0))))
    job.ai_reason = str(analysis.get("reason", ""))[:2000]
    job.experience_match = str(analysis.get("experience_match", ""))[:100]
    if analysis.get("category"):
        from app.services.resume_store import category_slug

        job.resume_category = category_slug(analysis.get("category"))
    return job
