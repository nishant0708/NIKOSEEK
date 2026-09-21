-- Run in Supabase Dashboard > SQL Editor once per project.
-- This provisions the private bucket. The FastAPI service uses its server-only
-- service-role key for uploads, so it is never exposed to browser clients.
insert into storage.buckets (id, name, public)
values ('job-files', 'job-files', false)
on conflict (id) do update set public = false;

-- Our current API authenticates with its own username/password JWT rather
-- than Supabase Auth. Keep the bucket private; the server mediates access.
alter table storage.objects enable row level security;
