# Connect Deeply

Pre-meeting research + common-ground conversations. Terminal CLI today, mobile app MVP next.

## Architecture

- `backend/` — research connectors, synthesis, common-ground, FastAPI for the app
- `mobile/` — Expo (React Native) app
- Signup captures **hobbies / interests / sports** so overlap quality does not depend on scraping your socials. When someone else joins later, the same fields become their social signal.

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

## CLI (unchanged)

```bash
cd backend
python3 cli.py --name "Someone" --company "..." --tier detailed
```

Instagram/Facebook/Twitter stay off unless you pass `--social`.

## What’s next

- Direct LinkedIn connection send
- Contact enrichment (email/phone)
- Auto-refine your profile from interaction feedback
- Real payments / token top-ups
