-- Public vs private entities + richer people rows
-- Apply in Supabase SQL editor after schema.sql

alter table if exists public.people
  add column if not exists university text,
  add column if not exists public_dossier jsonb not null default '{}'::jsonb,
  add column if not exists claimed_user_id uuid references public.users(id) on delete set null;

-- Private journal / daily posts (owner only; used for overlap fuel)
create table if not exists public.user_private_journal (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  entry_type text not null default 'note',  -- note | blog | insight
  body text not null,
  tags jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists user_private_journal_user_idx on public.user_private_journal(user_id);

alter table if exists public.user_private_journal disable row level security;
