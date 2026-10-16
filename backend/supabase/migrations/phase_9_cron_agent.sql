-- Phase 9: scheduled/ambient fetch agent. Safe to run more than once.
alter table scraped_jobs add column if not exists fetched_by_cron boolean not null default false;
create index if not exists scraped_jobs_user_cron_idx on scraped_jobs(user_id, fetched_by_cron, scraped_at desc);
