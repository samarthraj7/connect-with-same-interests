"""Ephemeral research drafts — not in people DB until rated good."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

DRAFT_TTL_HOURS = 24
_ROOT = Path(__file__).resolve().parent / "profiles" / "_drafts"


def _dir() -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    return _ROOT


def save_draft(payload: dict[str, Any]) -> str:
    draft_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    rec = {
        **payload,
        "draft_id": draft_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=DRAFT_TTL_HOURS)).isoformat(),
        "status": "pending_review",
    }
    path = _dir() / f"{draft_id}.json"
    path.write_text(json.dumps(rec, indent=2))
    print(f"  [draft] saved {draft_id} for {payload.get('name')!r}", flush=True)
    return draft_id


def load_draft(draft_id: str) -> Optional[dict[str, Any]]:
    path = _dir() / f"{(draft_id or '').strip()}.json"
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    exp = rec.get("expires_at")
    if exp:
        try:
            if datetime.fromisoformat(exp.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                path.unlink(missing_ok=True)
                return None
        except ValueError:
            pass
    return rec


def delete_draft(draft_id: str) -> None:
    path = _dir() / f"{(draft_id or '').strip()}.json"
    if path.exists():
        path.unlink()
        print(f"  [draft] deleted {draft_id}", flush=True)
