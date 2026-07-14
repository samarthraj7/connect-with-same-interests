"""Google Calendar auto-prep (Phase D).

OAuth link + queue upcoming attendees for research.
Requires GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET when enabling.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode


def calendar_configured() -> bool:
    return bool(
        (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
        and (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    )


def oauth_authorize_url(redirect_uri: str, state: str) -> dict[str, Any]:
    if not calendar_configured():
        return {"status": "skipped", "reason": "GOOGLE_CLIENT_ID/SECRET not set"}
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return {
        "status": "ok",
        "url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params),
    }


def exchange_code(code: str, redirect_uri: str) -> dict[str, Any]:
    if not calendar_configured():
        return {"status": "skipped", "reason": "Google OAuth not configured"}
    import requests

    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"].strip(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        return {"status": "error", "error": resp.text[:400]}
    data = resp.json()
    return {
        "status": "ok",
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
    }


def list_upcoming_attendees(access_token: str, *, hours_ahead: int = 72) -> dict[str, Any]:
    """Fetch calendar events and extract unique attendee emails/names for prep queue."""
    import requests

    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=hours_ahead)
    params = {
        "timeMin": now.isoformat(),
        "timeMax": end.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 40,
    }
    resp = requests.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=30,
    )
    if resp.status_code >= 400:
        return {"status": "error", "error": resp.text[:400]}
    events = resp.json().get("items") or []
    attendees: dict[str, dict] = {}
    for ev in events:
        start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
        for a in ev.get("attendees") or []:
            email = (a.get("email") or "").lower()
            if not email or a.get("self"):
                continue
            name = a.get("displayName") or email.split("@")[0].replace(".", " ").title()
            key = email
            if key not in attendees:
                attendees[key] = {
                    "attendee_name": name,
                    "attendee_email": email,
                    "meeting_at": start,
                    "event_summary": ev.get("summary"),
                }
    return {"status": "ok", "attendees": list(attendees.values()), "event_count": len(events)}


def store_calendar_link(user_store, user_id: str, tokens: dict) -> None:
    user = user_store.get(user_id)
    if not user:
        return
    settings = dict(user.get("settings") or {})
    settings["calendar"] = {
        "provider": "google",
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "auto_prep": True,
        "linked_at": datetime.now(timezone.utc).isoformat(),
    }
    user["settings"] = settings
    user["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = user_store._path(user_id)
    import json

    path.write_text(json.dumps(user, indent=2))


def enqueue_from_calendar(user_store, user_id: str) -> dict[str, Any]:
    user = user_store.get(user_id)
    if not user:
        return {"status": "error", "error": "user not found"}
    cal = (user.get("settings") or {}).get("calendar") or {}
    token = cal.get("access_token")
    if not token:
        return {"status": "skipped", "reason": "Calendar not linked"}
    listed = list_upcoming_attendees(token)
    if listed.get("status") != "ok":
        return listed
    queue = list((user.get("settings") or {}).get("meeting_prep_queue") or [])
    existing = {(q.get("attendee_email") or "").lower() for q in queue}
    added = 0
    for a in listed.get("attendees") or []:
        email = (a.get("attendee_email") or "").lower()
        if email in existing:
            continue
        queue.append({**a, "status": "queued", "queued_at": datetime.now(timezone.utc).isoformat()})
        existing.add(email)
        added += 1
    settings = dict(user.get("settings") or {})
    settings["meeting_prep_queue"] = queue[-100:]
    user["settings"] = settings
    user["updated_at"] = datetime.now(timezone.utc).isoformat()
    import json

    user_store._path(user_id).write_text(json.dumps(user, indent=2))
    return {"status": "ok", "added": added, "queue_size": len(queue)}
