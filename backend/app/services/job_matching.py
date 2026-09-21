import json

from app.schemas.jobs import JobRead, JobScrapeRequest
from app.services.llm import generate_json

MATCH_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "OBJECT", "properties": {
        "job_number": {"type": "INTEGER"}, "accepted": {"type": "BOOLEAN"},
        "match_score": {"type": "NUMBER"}, "experience_match": {"type": "STRING"}, "reason": {"type": "STRING"},
    }, "required": ["job_number", "accepted", "match_score", "experience_match", "reason"]},
}


def score_jobs(jobs: list[dict], profile_markdown: str, details_markdown: str, preferences: JobScrapeRequest, model: str, system_prompt: str) -> tuple[list[dict], dict[str, int]]:
    payload = [{"job_number": index + 1, **job} for index, job in enumerate(jobs)]
    replacements = {
        "profile_markdown": profile_markdown[:12000], "details_markdown": details_markdown[:8000],
        "search_term": preferences.search_term, "experience": preferences.experience,
        "min_salary_lpa": str(preferences.min_salary_lpa or "not specified"), "location": preferences.location,
        "work_preference": "remote" if preferences.is_remote else "any",
    }
    system = system_prompt
    for key, value in replacements.items():
        system = system.replace("{" + key + "}", value)
    result = generate_json("Evaluate these jobs:\n" + json.dumps(payload), system, MATCH_SCHEMA, model=model)
    return result.data, {"input": result.input_tokens, "output": result.output_tokens, "total": result.total_tokens}


def attach_analysis(job: JobRead, analysis: dict) -> JobRead:
    job.ai_status = "ACCEPTED" if analysis.get("accepted") else "REJECTED"
    job.match_score = max(0, min(100, float(analysis.get("match_score", 0))))
    job.ai_reason = str(analysis.get("reason", ""))[:2000]
    job.experience_match = str(analysis.get("experience_match", ""))[:100]
    return job
