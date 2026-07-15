#!/usr/bin/env python3
"""Migrate JSON users/ + profiles/ into Supabase (dual-write cutover helper).

Usage (from backend/):
  python migrate_to_supabase.py

Requires SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY and schema.sql applied.
JSON files remain readable; this pushes a snapshot into Postgres.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from api.users import UserStore  # noqa: E402
from db import get_supabase, supabase_enabled, upsert_person_snapshot, upsert_user_row  # noqa: E402
from storage import ProfileStore  # noqa: E402


def main() -> int:
    if not supabase_enabled():
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — nothing to migrate.")
        print("Apply backend/sql/schema.sql in your project, then set env vars.")
        return 1
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or ""
    ).strip()
    if key.startswith("sb_publishable"):
        print(
            "ERROR: SUPABASE_SERVICE_ROLE_KEY is a publishable key.\n"
            "Open Supabase → Project Settings → API → copy service_role (secret), put it in backend/.env,\n"
            "then apply backend/sql/schema.sql in the SQL Editor and re-run this script."
        )
        return 2
    sb = get_supabase()
    if not sb:
        print("Could not create Supabase client.")
        return 1
    # Sanity-check tables exist
    try:
        sb.table("people").select("id").limit(1).execute()
    except Exception as e:
        print(
            f"ERROR: tables not found ({e}).\n"
            "Open Supabase SQL Editor → paste/run the contents of backend/sql/schema.sql → then re-run."
        )
        return 3

    users = UserStore()
    profiles = ProfileStore()
    n_users = 0
    for user in users.list_all():
        upsert_user_row(user)
        conns = user.get("connections") or []
        if conns:
            try:
                users.replace_connections(user["id"], conns)
            except Exception as e:
                print(f"  connections for {user.get('email')}: {e}")
        n_users += 1
        print(f"  user {user.get('email')}")

    n_people = 0
    for path in profiles.base_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        name = record.get("name") or path.stem
        company = record.get("company")
        upsert_person_snapshot(
            slug=path.stem,
            name=name,
            company=company,
            contact=record.get("contact") or {},
            sources=record.get("latest_sources") or {},
            summary=record.get("latest_summary") or {},
            conversation_engine=record.get("latest_common_ground"),
            fingerprints=record.get("content_fingerprints"),
        )
        n_people += 1
        print(f"  person {path.stem}")

    print(f"Done. Migrated {n_users} users, {n_people} people.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
