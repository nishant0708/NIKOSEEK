# Backend quick start

This backend uses **Supabase Postgres** and a **private Supabase Storage bucket**. It does not start or use a local database/filesystem service.

1. Create a Supabase project. In its SQL Editor, run `supabase/setup.sql` to create the private `job-files` bucket.
2. In Supabase Dashboard > Connect, copy the SQLAlchemy-compatible Postgres connection string into `DATABASE_URL`. Use the database password, not an API key.
3. In Supabase Dashboard > Settings > API, copy the project URL and **service_role** key into `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. This key is backend-only and must never be placed in frontend code.
4. Copy `.env.example` to `.env`; set a long, random `JWT_SECRET_KEY`.
5. Set `GEMINI_API_KEY` to your Vertex AI Express Mode API key. The backend routes Gemini profile generation, job scoring, and application workflows through Vertex AI. Optionally change `AI_MODEL`; the default is `gemini-3.5-flash-lite`.
6. Optionally set `GROQ_API_KEY` to enable Groq's free-tier fallback when all configured Gemini models fail. The supplied default is the open-weight `openai/gpt-oss-20b` model; keep all provider keys in the backend environment only.
7. Run `supabase/migrations/phase_2_matching.sql` in the Supabase SQL editor to add search-run and AI-score fields.
8. Create a virtual environment, install requirements, and run: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python run.py`

Interactive API documentation is at `http://localhost:8000/docs`.

All API routes below `/api/v1` except signup/login require `Authorization: Bearer <access_token>`.

Supabase Storage uses virtual object prefixes and creates this layout on signup:

```
<username>/
  Profile/profile.md
  Resumes/<role>/
  Applied Resumes/
```

`PUT /api/v1/profile` writes the Markdown profile file. `POST /api/v1/profile/upload` accepts a Markdown file and `/template` supplies a starter. A profile database table is deliberately not created. `POST /api/v1/resumes` accepts multipart fields `role` and `file` (PDF/DOCX, max 5 MB). `POST /api/v1/jobs/scrape` accepts JobSpy sources, search term, location, job type and basic filters.

## AI matching flow

1. Upload a resume, then `POST /api/v1/profile/generate` with its `storage_key`, extra project details, and achievements. This saves private `Profile/details.md` and generated `Profile/profile.md` objects in Supabase Storage.
2. Review and edit `profile.md` using `GET`/`PUT /api/v1/profile`.
3. Send job-search preferences (`experience`, `min_salary_lpa`, and `target_relevant_count` included) to `POST /api/v1/jobs/scrape`.
4. Gemini scores every scraped job. The response separates `accepted_jobs` and `rejected_jobs`; each has `match_score`, `ai_reason`, and `experience_match`. `GET /api/v1/jobs/runs` supplies the run state for a React Flow status UI.

## Application queue

1. `GET /api/v1/resumes` lists the resume objects available for the current user.
2. `POST /api/v1/applications/apply` queues selected accepted jobs, stores the resume key used, and creates a `job_applications` row for each job.
3. `GET /api/v1/applications` returns the current queue state.
4. `WS /api/v1/applications/ws?token=...` streams live application updates so the workflow UI can glow on the active step.

Automatic submission is an opt-in setting in Workflow configuration. To send post-submission receipts, configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM_EMAIL` (and optionally `SMTP_RECEIPT_EMAIL` and `SMTP_USE_TLS`) in the backend environment. The recipient defaults to the user's saved LinkedIn or Indeed email. Without SMTP configuration, applications still submit and the backend logs that the receipt was skipped.
