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

# Keep each request small enough that a search can stop before scoring every
# scraped posting. A batch may contain a few jobs beyond the final accepted
# result, but no later batches are sent to the model.
SCORING_BATCH_SIZE = 5


def _score_batch(jobs: list[tuple[int, dict]], profile_markdown: str, details_markdown: str, preferences: JobScrapeRequest, model: str, system_prompt: str) -> tuple[list[dict], dict[str, int]]:
    payload = [{"job_number": number, **job} for number, job in jobs]
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


def score_jobs(jobs: list[dict], profile_markdown: str, details_markdown: str, preferences: JobScrapeRequest, model: str, system_prompt: str) -> tuple[list[dict], dict[str, int]]:
    return _score_batch(list(enumerate(jobs, start=1)), profile_markdown, details_markdown, preferences, model, system_prompt)


def score_jobs_until_target(jobs: list[dict], profile_markdown: str, details_markdown: str, preferences: JobScrapeRequest, model: str, system_prompt: str) -> tuple[list[dict], dict[str, int]]:
    """Score ordered jobs in batches, making no further LLM calls after the target is met."""
    analyses: list[dict] = []
    usage = {"input": 0, "output": 0, "total": 0}
    accepted_count = 0

    for start in range(0, len(jobs), SCORING_BATCH_SIZE):
        batch = list(enumerate(jobs[start:start + SCORING_BATCH_SIZE], start=start + 1))
        batch_analyses, batch_usage = _score_batch(batch, profile_markdown, details_markdown, preferences, model, system_prompt)
        for key in usage:
            usage[key] += batch_usage[key]

        by_number = {item.get("job_number"): item for item in batch_analyses}
        for number, _ in batch:
            analysis = by_number.get(number, {"job_number": number, "accepted": False, "match_score": 0, "reason": "No score returned", "experience_match": "Unknown"})
            analyses.append(analysis)
            if analysis.get("accepted"):
                accepted_count += 1
                if accepted_count >= preferences.target_relevant_count:
                    return analyses, usage

    return analyses, usage


def attach_analysis(job: JobRead, analysis: dict) -> JobRead:
    job.ai_status = "ACCEPTED" if analysis.get("accepted") else "REJECTED"
    job.match_score = max(0, min(100, float(analysis.get("match_score", 0))))
    job.ai_reason = str(analysis.get("reason", ""))[:2000]
    job.experience_match = str(analysis.get("experience_match", ""))[:100]
    return job
