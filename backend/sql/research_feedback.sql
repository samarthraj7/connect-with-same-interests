-- Research quality ratings (good / bad) + correction notes for next run.
-- Apply in Supabase SQL editor after schema.sql

create table if not exists public.research_feedback (
  id text primary key,
  user_id uuid references public.users(id) on delete set null,
  person_id uuid references public.people(id) on delete set null,
  person_slug text,
  draft_id text,
  name text not null,
  company text,
  linkedin_url text,
  rating text not null check (rating in ('good', 'bad')),
  wrong_notes text,
  wrong_categories jsonb not null default '[]'::jsonb,
  briefing_snapshot jsonb,
  applied_on_next_research boolean not null default false,
  created_at timestamptz not null default now()
);

create index if not exists research_feedback_li_idx
  on public.research_feedback (linkedin_url)
  where linkedin_url is not null;

create index if not exists research_feedback_name_idx
  on public.research_feedback (lower(name), lower(coalesce(company, '')));

create index if not exists research_feedback_created_idx
  on public.research_feedback (created_at desc);

alter table if exists public.research_feedback disable row level security;
