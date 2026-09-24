# Backend quick start

This backend uses **Supabase Postgres** and a **private Supabase Storage bucket**. It does not start or use a local database/filesystem service.

1. Create a Supabase project. In its SQL Editor, run `supabase/setup.sql` to create the private `job-files` bucket.
2. In Supabase Dashboard > Connect, copy the SQLAlchemy-compatible Postgres connection string into `DATABASE_URL`. Use the database password, not an API key.
3. In Supabase Dashboard > Settings > API, copy the project URL and **service_role** key into `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. This key is backend-only and must never be placed in frontend code.
4. Copy `.env.example` to `.env`; set a long, random `JWT_SECRET_KEY`.
5. Set `GEMINI_API_KEY` (the backend uses Gemini to generate profiles and score job matches) and optionally change `AI_MODEL`. The default is `gemini-3.5-flash-lite`.
6. Run `supabase/migrations/phase_2_matching.sql` in the Supabase SQL editor to add search-run and AI-score fields.
7. Create a virtual environment, install requirements, and run: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python run.py`

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
3. Send job-search preferences (`experience`, `min_salary_lpa`, and `target_relevant_count` included) to `POST /api/v1/jobs/scrape`. LinkedIn, Indeed, and Naukri are supported; Indeed additionally requires `indeed_country`.
4. Gemini scores jobs in small batches until it finds `target_relevant_count` relevant results, then stops before scoring more batches to avoid consuming tokens on the remaining listings. The response separates `accepted_jobs` and `rejected_jobs`; each has `match_score`, `ai_reason`, and `experience_match`. `GET /api/v1/jobs/runs` supplies the run state for a React Flow status UI.
