"""Optional Supabase/Postgres client. Falls back to None when unset.

Uses SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY.
When unavailable, JSON stores remain authoritative.

Apply schema first: backend/sql/schema.sql (+ sql/rls_fix.sql if RLS is on).
Use the service_role secret, not the publishable/anon key.
"""

from __future__ import annotations

import os
from typing import Any, Optional

_client = None
_checked = False


def supabase_enabled() -> bool:
    return bool(
        (os.environ.get("SUPABASE_URL") or "").strip()
        and (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()
    )


def _api_key() -> str:
    return (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or ""
    ).strip()


def get_supabase():
    """Lazy-init supabase-py client, or None."""
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    if not supabase_enabled():
        _client = None
        return None
    try:
        from supabase import create_client

        url = os.environ["SUPABASE_URL"].strip()
        key = _api_key()
        if key.startswith("sb_publishable"):
            print(
                "[supabase] WARNING: SUPABASE_SERVICE_ROLE_KEY looks like a publishable/anon key. "
                "Use Project Settings → API → service_role secret instead."
            )
        _client = create_client(url, key)
        return _client
    except Exception as e:
        print(f"[supabase] client init failed: {e}")
        _client = None
        return None


def upsert_user_row(user: dict[str, Any]) -> None:
    sb = get_supabase()
    if not sb:
        return
    try:
        sb.table("users").upsert(
            {
                "id": user["id"],
                "email": user["email"],
                "password_hash": user["password_hash"],
                "tokens": user.get("tokens", 0),
                "token_ledger": user.get("token_ledger") or [],
                "settings": user.get("settings") or {},
                "profile_refinement": user.get("profile_refinement"),
                "updated_at": user.get("updated_at"),
            }
        ).execute()
        profile = user.get("profile") or {}
        sb.table("user_profiles").upsert(
            {
                "user_id": user["id"],
                "profile": profile,
                "signup_form": profile.get("signup_form"),
                "socials": (profile.get("contact") or {}),
                "verification": profile.get("handle_verification") or {},
            }
        ).execute()
    except Exception as e:
        err = str(e)
        if "42501" in err or "row-level security" in err.lower():
            print(
                f"[supabase] upsert_user failed (RLS): {e}\n"
                "  → Run backend/sql/rls_fix.sql in the Supabase SQL editor, "
                "and use the service_role secret in .env."
            )
        else:
            print(f"[supabase] upsert_user failed: {e}")


def upsert_person_snapshot(
    *,
    slug: str,
    name: str,
    company: Optional[str],
    contact: dict,
    sources: dict,
    summary: dict,
    conversation_engine: Optional[dict] = None,
    fingerprints: Optional[dict] = None,
) -> None:
    sb = get_supabase()
    if not sb:
        return
    try:
        row = (
            sb.table("people")
            .upsert(
                {
                    "slug": slug,
                    "name": name,
                    "company": company,
                    "linkedin_url": (contact or {}).get("linkedin_url"),
                    "contact": contact or {},
                    "content_fingerprint": (fingerprints or {}).get("_all"),
                },
                on_conflict="slug",
            )
            .execute()
        )
        people = row.data or []
        person_id = people[0]["id"] if people else None
        if not person_id:
            found = sb.table("people").select("id").eq("slug", slug).limit(1).execute()
            person_id = (found.data or [{}])[0].get("id")
        if not person_id:
            return
        for source, payload in (sources or {}).items():
            sb.table("person_sources").upsert(
                {
                    "person_id": person_id,
                    "source": source,
                    "payload": payload,
                    "content_fingerprint": (fingerprints or {}).get(source),
                },
                on_conflict="person_id,source",
            ).execute()
        sb.table("person_summaries").upsert(
            {"person_id": person_id, "briefing": summary or {}}
        ).execute()
        if conversation_engine:
            from common_ground import public_conversation

            pub = public_conversation(conversation_engine)
            sb.table("conversations").upsert(
                {
                    "person_id": person_id,
                    "talk_about": pub.get("talk_about"),
                    "openers": pub.get("openers"),
                    "deep_questions": pub.get("deep_questions"),
                    "engine": conversation_engine,
                }
            ).execute()
    except Exception as e:
        err = str(e)
        if "42501" in err or "row-level security" in err.lower():
            print(
                f"[supabase] upsert_person failed (RLS): {e}\n"
                "  → Run backend/sql/rls_fix.sql in the Supabase SQL editor."
            )
        else:
            print(f"[supabase] upsert_person failed: {e}")
