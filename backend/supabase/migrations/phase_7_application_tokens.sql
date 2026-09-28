-- Phase 7: AI token usage per application (Postgres / Supabase). Safe to run more than once.
alter table job_applications add column if not exists llm_input_tokens integer not null default 0;
alter table job_applications add column if not exists llm_output_tokens integer not null default 0;
alter table job_applications add column if not exists llm_total_tokens integer not null default 0;
alter table job_applications add column if not exists llm_calls integer not null default 0;
