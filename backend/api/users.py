"""File-backed user store (MVP). Swap for Postgres later."""

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
            "created_at": now,
            "updated_at": now,
        }
        self._path(user_id).write_text(json.dumps(user, indent=2))
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
        self._path(user_id).write_text(json.dumps(user, indent=2))
        return user

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
        self._path(user_id).write_text(json.dumps(user, indent=2))
        return user

    def append_interaction(self, user_id: str, event: dict[str, Any]) -> dict:
        user = self.get(user_id)
        if not user:
            raise KeyError(user_id)
        entry = {"at": datetime.now(timezone.utc).isoformat(), **event}
        user.setdefault("interactions", []).append(entry)
        user["updated_at"] = entry["at"]
        self._path(user_id).write_text(json.dumps(user, indent=2))
        return user
