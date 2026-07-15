"""File-backed user store with optional Supabase dual-write."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

USERS_DIR = Path(__file__).resolve().parent.parent / "users"


class UserStore:
    def __init__(self, base_dir: Path = USERS_DIR):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "_index.json"
        if not self.index_path.exists():
            self.index_path.write_text(json.dumps({"email_to_id": {}}, indent=2))

    def _index(self) -> dict:
        return json.loads(self.index_path.read_text())

    def _save_index(self, index: dict) -> None:
        self.index_path.write_text(json.dumps(index, indent=2))

    def _path(self, user_id: str) -> Path:
        return self.base_dir / f"{user_id}.json"

    def _write(self, user: dict) -> dict:
        self._path(user["id"]).write_text(json.dumps(user, indent=2))
        try:
            from db import upsert_user_row

            upsert_user_row(user)
        except Exception:
            pass
        return user

    def create(
        self,
        *,
        email: str,
        password_hash: str,
        profile: dict,
        starting_tokens: int = 15,
    ) -> dict:
        index = self._index()
        if email in index["email_to_id"]:
            raise ValueError("Email already registered")
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        user = {
            "id": user_id,
            "email": email,
            "password_hash": password_hash,
            "tokens": starting_tokens,
            "token_ledger": [
                {
                    "at": now,
                    "delta": starting_tokens,
                    "reason": "signup_grant",
                    "balance": starting_tokens,
                }
            ],
            "profile": profile,
            "interactions": [],
            "connections": [],
            "settings": {"theme": "light"},
            "pending_facts": [],
            "created_at": now,
            "updated_at": now,
        }
        self._write(user)
        index["email_to_id"][email] = user_id
        self._save_index(index)
        return user

    def get(self, user_id: str) -> Optional[dict]:
        path = self._path(user_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def find_by_email(self, email: str) -> Optional[dict]:
        user_id = self._index()["email_to_id"].get(email.lower())
        if not user_id:
            return None
        return self.get(user_id)

    def update_profile(self, user_id: str, updates: dict[str, Any]) -> dict:
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        profile = dict(user.get("profile") or {})
        refinement = updates.pop("profile_refinement", None)
        for k, v in updates.items():
            profile[k] = v
        user["profile"] = profile
        if refinement is not None:
            user["profile_refinement"] = refinement
            profile["profile_refinement"] = refinement
            user["profile"] = profile
        user["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._write(user)

    def replace_profile(self, user_id: str, profile: dict[str, Any]) -> dict:
        """Overwrite the whole living YOU profile (e.g. after self-research)."""
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        user["profile"] = profile
        user["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._write(user)

    def update_settings(self, user_id: str, settings_patch: dict[str, Any]) -> dict:
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        settings = dict(user.get("settings") or {})
        settings.update(settings_patch)
        user["settings"] = settings
        user["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._write(user)

    def replace_connections(self, user_id: str, connections: list[dict]) -> dict:
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        user["connections"] = connections
        user["connections_imported_at"] = datetime.now(timezone.utc).isoformat()
        user["updated_at"] = user["connections_imported_at"]
        written = self._write(user)
        try:
            from db import get_supabase

            sb = get_supabase()
            if sb:
                sb.table("user_connections").delete().eq("user_id", user_id).execute()
                rows = [
                    {
                        "user_id": user_id,
                        "name": c.get("name"),
                        "company": c.get("company"),
                        "linkedin_url": c.get("linkedin_url"),
                        "connected_on": c.get("connected_on"),
                        "email": c.get("email"),
                        "raw": c.get("raw") or {},
                    }
                    for c in connections
                    if c.get("name")
                ]
                for i in range(0, len(rows), 200):
                    sb.table("user_connections").insert(rows[i : i + 200]).execute()
        except Exception as e:
            print(f"[connections] supabase write skipped: {e}")
        return written

    def add_pending_fact(self, user_id: str, fact: dict) -> dict:
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        entry = {
            "id": str(uuid.uuid4()),
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            **fact,
        }
        user.setdefault("pending_facts", []).append(entry)
        user["updated_at"] = entry["created_at"]
        return self._write(user)

    def update_pending_fact(
        self, user_id: str, fact_id: str, status: str, evidence: Any = None
    ) -> dict:
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        facts = user.get("pending_facts") or []
        for f in facts:
            if f.get("id") == fact_id:
                f["status"] = status
                if evidence is not None:
                    f["evidence"] = evidence
                f["updated_at"] = datetime.now(timezone.utc).isoformat()
                break
        user["pending_facts"] = facts
        user["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._write(user)

    def charge_tokens(self, user_id: str, amount: int, reason: str) -> dict:
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        balance = int(user.get("tokens") or 0)
        if balance < amount:
            raise ValueError("Insufficient tokens")
        balance -= amount
        user["tokens"] = balance
        user.setdefault("token_ledger", []).append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "delta": -amount,
                "reason": reason,
                "balance": balance,
            }
        )
        user["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._write(user)

    def append_interaction(self, user_id: str, event: dict[str, Any]) -> dict:
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        entry = {"at": datetime.now(timezone.utc).isoformat(), **event}
        user.setdefault("interactions", []).append(entry)
        user["updated_at"] = entry["at"]
        return self._write(user)

    def list_all(self) -> list[dict]:
        users = []
        for path in self.base_dir.glob("*.json"):
            if path.name.startswith("_"):
                continue
            try:
                users.append(json.loads(path.read_text()))
            except json.JSONDecodeError:
                continue
        return users
