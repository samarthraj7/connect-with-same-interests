# Connect Deeply

Pre-meeting research + common-ground conversations. Terminal CLI today, mobile app MVP next.

## Architecture

- `backend/` — research connectors, synthesis, common-ground, FastAPI for the app
- `mobile/` — Expo (React Native) app
- **Signup researches YOU** (form + socials) and saves the full account to JSON under `backend/users/`.
- Overlap is an internal step that produces talk topics / openers (not shown as a score).

## Backend API

```bash
cd backend
python3 -m pip install -r requirements.txt
# ensure .env has GEMINI_API_KEY (and other keys you use)
python3 -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Token costs: **basic = 1**, **detailed (overlap + questions) = 3**. New accounts start with `STARTING_TOKENS` (default 15).

### Key routes

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/auth/signup` | Account + profile (hobbies/interests) |
| POST | `/auth/login` | Login |
| GET/PATCH | `/me`, `/me/profile` | You + refine hobbies |
| POST | `/candidates` | Disambiguation |
| POST | `/research` | Research + optional common ground |
| GET | `/people` | CRM list |
| GET | `/people/{name}` | Saved briefing |

## Mobile app

Requires Node 20+.

```bash
cd mobile
npm install
# optional: point at your machine
# export EXPO_PUBLIC_API_URL=http://YOUR_LAN_IP:8000
npm start
```

Then open in iOS Simulator, Android emulator, or Expo Go. Physical devices need `EXPO_PUBLIC_API_URL` set to your computer’s LAN IP (API must be running with `--host 0.0.0.0`).

## CLI

```bash
cd backend
python3 cli.py --name "Someone" --tier detailed
```

You’ll pick **full name + company**, then deep dive + detailed conversation ideas run. Thin public footprints prompt you for LinkedIn / Instagram / school facts.

## Data storage (MVP)

Accounts and researched people are stored as **JSON files** (swap for Postgres later):

| Path | Contents |
|------|----------|
| `backend/users/<id>.json` | Email, password hash, tokens, full profile, signup form, socials |
| `backend/users/_index.json` | email → user id |
| `backend/profiles/*.json` | Researched people + conversation engine cache |
| `backend/interactions/*.jsonl` | CRM-style event log |

`.env` secrets and `users/` / `profiles/` are gitignored.
