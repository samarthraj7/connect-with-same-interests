"""Private journal for account holders — never shown on public person pages.

Used only as overlap fuel when someone else researches this claimed person.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def list_entries(user: dict) -> list[dict]:
    return list((user.get("private") or {}).get("journal") or [])


def add_entry(user_store, user_id: str, *, body: str, entry_type: str = "note", tags: Optional[list] = None) -> dict:
    user = user_store.get(user_id)
    if not user:
        raise KeyError(user_id)
    private = dict(user.get("private") or {})
    journal = list(private.get("journal") or [])
    entry = {
        "id": str(uuid.uuid4()),
        "type": entry_type or "note",
        "body": (body or "").strip(),
        "tags": [t for t in (tags or []) if t],
        "at": datetime.now(timezone.utc).isoformat(),
        "visibility": "private",
    }
    if not entry["body"]:
        raise ValueError("body required")
    journal.insert(0, entry)
    private["journal"] = journal[:200]
    user["private"] = private
    user["updated_at"] = entry["at"]
    user_store._write(user)
    return entry


def delete_entry(user_store, user_id: str, entry_id: str) -> bool:
    user = user_store.get(user_id)
    if not user:
        return False
    private = dict(user.get("private") or {})
    journal = [e for e in (private.get("journal") or []) if e.get("id") != entry_id]
    private["journal"] = journal
    user["private"] = private
    user_store._write(user)
    return True


def overlap_hints_from_private(user: Optional[dict]) -> dict[str, Any]:
    """Compact private signal for the overlap engine (never returned to searchers as raw)."""
    if not user:
        return {}
    journal = list_entries(user)
    profile = user.get("profile") or {}
    return {
        "private_interests": profile.get("interests") or [],
        "private_hobbies": profile.get("hobbies") or [],
        "private_sports": profile.get("sports") or [],
        "private_journal_snippets": [
            {"type": e.get("type"), "body": (e.get("body") or "")[:400], "tags": e.get("tags") or []}
            for e in journal[:12]
        ],
    }


def find_user_claiming_person(user_store, *, name: str, company: Optional[str], slug: Optional[str]) -> Optional[dict]:
    """Find an app user whose public researched identity matches THEM."""
    name_l = (name or "").strip().lower()
    for u in user_store.list_all():
        profile = u.get("profile") or {}
        if slug and profile.get("self_profile_slug") == slug:
            return u
        if (profile.get("name") or "").strip().lower() != name_l:
            continue
        co = (profile.get("current_company") or company or "").strip().lower()
        their_co = (company or "").strip().lower()
        if their_co and co and their_co not in co and co not in their_co:
            continue
        if profile.get("self_profile_slug") or profile.get("profile_source") in (
            "researched_at_signup",
            "claimed_public",
        ):
            return u
    return None
