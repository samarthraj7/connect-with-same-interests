-- Connect Deeply — Supabase / Postgres schema
-- Apply in Supabase SQL editor or: psql $DATABASE_URL -f sql/schema.sql

create extension if not exists "pgcrypto";

-- App users (JWT auth can stay custom; optional link to auth.users later)
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  tokens int not null default 15,
  token_ledger jsonb not null default '[]'::jsonb,
  settings jsonb not null default '{}'::jsonb,  -- theme, calendar prefs
  profile_refinement jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.user_profiles (
  user_id uuid primary key references public.users(id) on delete cascade,
  profile jsonb not null default '{}'::jsonb,
  signup_form jsonb,
  socials jsonb,
  verification jsonb not null default '{}'::jsonb,  -- handle -> verified|ambiguous|rejected
  updated_at timestamptz not null default now()
);

create table if not exists public.user_connections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  name text not null,
  company text,
  linkedin_url text,
  connected_on text,
  email text,
  raw jsonb,
  created_at timestamptz not null default now()
);
create index if not exists user_connections_user_idx on public.user_connections(user_id);
create index if not exists user_connections_name_idx on public.user_connections(lower(name));

create table if not exists public.people (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  company text,
  linkedin_url text,
  contact jsonb not null default '{}'::jsonb,
  content_fingerprint text,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index if not exists people_name_company_idx on public.people(lower(name), lower(coalesce(company, '')));

create table if not exists public.person_sources (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references public.people(id) on delete cascade,
  source text not null,
  payload jsonb not null default '{}'::jsonb,
  fetched_at timestamptz not null default now(),
  content_fingerprint text,
  unique (person_id, source)
);

create table if not exists public.person_summaries (
  person_id uuid primary key references public.people(id) on delete cascade,
  briefing jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.conversations (
  person_id uuid primary key references public.people(id) on delete cascade,
  talk_about jsonb,
  openers jsonb,
  deep_questions jsonb,
  engine jsonb,  -- internal overlap; not for clients
  updated_at timestamptz not null default now()
);

create table if not exists public.interactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.users(id) on delete set null,
  person_id uuid references public.people(id) on delete cascade,
  type text not null default 'note',
  note text,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.pending_facts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  person_id uuid references public.people(id) on delete cascade,
  claim text not null,
  status text not null default 'pending',  -- pending | corroborated | trusted_personal | rejected
  evidence jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.whats_new (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references public.people(id) on delete cascade,
  seen_by_user_id uuid references public.users(id) on delete cascade,
  diff jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.calendar_links (
  user_id uuid primary key references public.users(id) on delete cascade,
  provider text not null default 'google',
  access_token text,
  refresh_token text,
  token_expiry timestamptz,
  calendar_id text,
  auto_prep boolean not null default false,
  updated_at timestamptz not null default now()
);

create table if not exists public.meeting_prep_queue (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  attendee_name text not null,
  attendee_email text,
  company text,
  meeting_at timestamptz,
  status text not null default 'queued',  -- queued | researching | ready | failed
  person_slug text,
  created_at timestamptz not null default now()
);

-- Public dossier richness + claim link
alter table if exists public.people
  add column if not exists university text;
alter table if exists public.people
  add column if not exists public_dossier jsonb not null default '{}'::jsonb;
alter table if exists public.people
  add column if not exists claimed_user_id uuid references public.users(id) on delete set null;

-- Private journal (owner only; overlap fuel)
create table if not exists public.user_private_journal (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  entry_type text not null default 'note',
  body text not null,
  tags jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists user_private_journal_user_idx on public.user_private_journal(user_id);

-- Backend uses FastAPI JWT + service_role. Disable RLS so dual-write isn't blocked
-- when the project default is "enable RLS". Revisit when moving to Supabase Auth.
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
alter table if exists public.user_private_journal disable row level security;
