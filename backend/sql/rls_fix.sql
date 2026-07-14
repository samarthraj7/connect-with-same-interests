-- Fix RLS blocking backend dual-write (Connect Deeply uses FastAPI + service_role, not Supabase Auth).
-- Run this in the Supabase SQL Editor if you see: new row violates row-level security policy

alter table if exists public.users disable row level security;
alter table if exists public.user_profiles disable row level security;
alter table if exists public.user_connections disable row level security;
alter table if exists public.people disable row level security;
alter table if exists public.person_sources disable row level security;
alter table if exists public.person_summaries disable row level security;
alter table if exists public.conversations disable row level security;
alter table if exists public.interactions disable row level security;
alter table if exists public.pending_facts disable row level security;
alter table if exists public.whats_new disable row level security;
alter table if exists public.calendar_links disable row level security;
alter table if exists public.meeting_prep_queue disable row level security;

-- Optional: keep RLS on later when migrating to Supabase Auth; until then the
-- service_role secret + FastAPI JWT is the access control plane.
