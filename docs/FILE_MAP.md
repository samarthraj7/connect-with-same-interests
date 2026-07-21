# Connect Deeply — file guide

Purpose of each **source** file in the repo (excludes `node_modules/`, local `users/` / `profiles/` data dumps, `.env`, and build caches).

---

## Root

| File | Use |
|------|-----|
| `README.md` | Product overview, problem, customers, how to run, flow diagram |
| `docs/FILE_MAP.md` | This guide |

---

## Backend — API layer (`backend/api/`)

| File | Use |
|------|-----|
| `api/__init__.py` | Package marker for the `api` module |
| `api/main.py` | FastAPI app: auth, candidates, research, drafts/feedback, people CRM, me/profile, calendar prep hooks |
| `api/auth.py` | Password hashing, JWT create/verify, `require_user` dependency |
| `api/users.py` | User JSON store helpers (create, get, profile update, tokens, interactions) |

---

## Backend — research core

| File | Use |
|------|-----|
| `orchestrator.py` | Fans out a `PersonQuery` to connectors in waves (Apollo → parallel Gemini/Exa/personal/public_web/GitHub → LinkedIn public → optional socials) |
| `merge.py` | Combines connector results into one `sources` + `query` document for synthesis |
| `synthesize.py` | Gemini briefing: identity lock, citations, structured JSON summary |
| `common_ground.py` | Detailed-tier conversation engine (talk topics / openers from YOU vs THEM) |
| `identity_lock.py` | Normalize LinkedIn URLs, same-person checks, identity lock prompt text |
| `research_drafts.py` | Save/load/delete ephemeral drafts under `profiles/_drafts/` until rated |
| `research_feedback.py` | Record good/bad ratings, prior bad corrections for next synthesize, optional Supabase |
| `storage.py` | People profile files: save/load, freshness, contact seeding (canonical LinkedIn wins) |
| `user_profile.py` | Map research dumps ↔ flat YOU schema; `profile_from_research`, overlap helpers |
| `people_lookup.py` | Find prior people by name/company/LinkedIn; public dossier shaping |
| `freshness.py` | Content fingerprints + “what’s new” between research runs |
| `gemini_retry.py` | Retry Gemini `generate_content` on 5xx/overload |
| `query_agent.py` | LLM helper for query planning (CLI / sparse paths) |
| `sparse_profile.py` | Thin public footprint handling / prompts for sparse profiles |
| `handle_verify.py` | Verify signup socials (GitHub, LinkedIn URL, Instagram, Twitter) against the claimed person |
| `connections.py` | Import LinkedIn connections CSV; mutual / in-network matching |
| `private_journal.py` | Private journal entries for YOU (overlap only; never public dossier) |
| `calendar_prep.py` | Google Calendar attendee queue for meeting prep |
| `otp.py` | Email OTP helpers for auth flows |
| `db.py` | Optional Supabase client + upserts when configured |
| `migrate_to_supabase.py` | One-shot / maintenance migration of local JSON → Supabase |
| `cli.py` | Terminal UX: candidates → deep dive → optional social refresh |
| `requirements.txt` | Python dependencies |
| `.env.example` | Documented environment variables (copy to `.env`) |
| `user_profile.json` | Sample / default YOU profile shape for CLI overlap |
| `claude_research_prompt.md` | Legacy / reference prompt notes |

---

## Backend — connectors (`backend/connectors/`)

| File | Use |
|------|-----|
| `connectors/__init__.py` | Package marker |
| `apollo.py` | Apollo.io people enrich (email, title, LinkedIn, photo hints) |
| `enrichlayer.py` | Enrich Layer LinkedIn photo / profile by URL (Find Me photos) |
| `exa_search.py` | Exa: LinkedIn people-by-name (Find Me), deep mentions + LinkedIn URL lock |
| `gemini_search.py` | Gemini Google Search angles; Find Me candidate fallback; photo hunt helpers |
| `personal_info.py` | Milestone bio research (hometown, hobbies, family, etc.) via Gemini |
| `public_web.py` | Portfolios / sites / directories tied to identity |
| `github.py` | GitHub user/repo search |
| `patents.py` | PatentsView inventor search |
| `linkedin_public.py` | Logged-out single LinkedIn public profile fetch (optional flag) |
| `instagram.py` / `facebook.py` / `twitter.py` | Opt-in social scrapers (`fetch_social`) |
| `social_find.py` | Discover likely social handles |
| `social_verify.py` | Score whether a social profile matches the person |
| `opengraph.py` | Fetch OG image / meta for photo enrichment |
| `reform_query.py` | Reform agent: rewrite search queries for Exa/Gemini depth |

---

## Backend — SQL (`backend/sql/`)

| File | Use |
|------|-----|
| `schema.sql` | Core Supabase tables for people / snapshots |
| `public_private.sql` | Public dossier vs private journal separation |
| `rls_fix.sql` | Row-level security fix notes / policies |
| `research_feedback.sql` | Table for good/bad research feedback + corrections |

---

## Backend — runtime data dirs (gitignored contents)

| Path | Use |
|------|-----|
| `profiles/*.json` | Committed research dossiers per person |
| `profiles/_drafts/` | Pending rating drafts |
| `profiles/_feedback/` | Local feedback index |
| `users/*.json` | User accounts |
| `interactions/*.jsonl` | CRM interaction logs |

---

## Mobile — app screens (`mobile/app/`)

| File | Use |
|------|-----|
| `app/_layout.tsx` | Root Expo Router layout (fonts, providers) |
| `app/index.tsx` | Entry redirect (auth vs app) |
| `app/(auth)/_layout.tsx` | Auth stack layout |
| `app/(auth)/welcome.tsx` | Welcome / marketing entry |
| `app/(auth)/login.tsx` | Login screen |
| `app/(auth)/signup.tsx` | Auth-route signup (may wrap sheet) |
| `app/(app)/_layout.tsx` | Authenticated app shell |
| `app/(app)/home.tsx` | Find person: name, filters, candidates, run research |
| `app/(app)/person/[name].tsx` | Person briefing: talk ideas, dossier, Reach out, rating, notes |
| `app/(app)/profile.tsx` | YOU: full research sections, rate self-draft, edit, journal, re-research |
| `app/(app)/crm.tsx` | List of previously researched people |

---

## Mobile — components & libs

| File | Use |
|------|-----|
| `components/ui.tsx` | Brand, fields, buttons, Bullet/Body with **source hyperlinks** (`LinkedText`, `UrlLink`) |
| `components/SignupSheet.tsx` | Modal signup: Find Me → account → research me → Good/Bad → public/private |
| `components/AuthModal.tsx` | Login/signup modal helper |
| `components/ScreenBackdrop.tsx` | Branded gradient / backdrop for screens |
| `lib/api.ts` | REST client, `API_BASE` / `DEV_API_URL`, typed endpoints |
| `lib/auth.tsx` | Session token + user context, refresh |
| `lib/theme.ts` | Fonts, spacing, light palette tokens |
| `lib/theme-context.tsx` | Light/dark theme provider |
| `app.json` | Expo app config |
| `package.json` | npm scripts and RN/Expo deps |
| `tsconfig.json` | TypeScript config |
| `expo-env.d.ts` | Expo generated typings |

---

## End-to-end call path (files involved)

```
home.tsx / SignupSheet.tsx
  → lib/api.ts
    → api/main.py (/candidates or /research or /me/research)
      → connectors/gemini_search.py + exa_search.py   (Find Me)
      → orchestrator.py
        → connectors/*                                  (deep dive)
      → merge.py → synthesize.py → common_ground.py
      → research_drafts.py  or  storage.py
    → person/[name].tsx / profile.tsx
      → research_feedback.py on Good/Bad
```

---

## Intentionally not documented in detail

- `mobile/node_modules/**` — third-party packages  
- `.git/`, `.cursor/`, `.expo/` — tooling / caches  
- Local secrets: `backend/.env`, `mobile/.env`  
- Per-user JSON / JSONL under `users/`, `profiles/`, `interactions/` (runtime data)
