"""User ratings on research quality + correction notes for next run."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from identity_lock import normalize_linkedin_url

_ROOT = Path(__file__).resolve().parent / "profiles" / "_feedback"


def _dir() -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    return _ROOT


def _index_path() -> Path:
    return _dir() / "index.json"


def _load_index() -> list:
    p = _index_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _save_index(rows: list) -> None:
    _index_path().write_text(json.dumps(rows, indent=2))


def record_feedback(
    *,
    user_id: Optional[str],
    rating: str,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    person_slug: Optional[str] = None,
    draft_id: Optional[str] = None,
    wrong_notes: Optional[str] = None,
    wrong_categories: Optional[List[str]] = None,
    briefing_snapshot: Optional[dict] = None,
) -> dict[str, Any]:
    rating = (rating or "").strip().lower()
    if rating not in ("good", "bad"):
        raise ValueError("rating must be good or bad")

    row = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "rating": rating,
        "name": (name or "").strip(),
        "company": (company or "").strip() or None,
        "university": (university or "").strip() or None,
        "linkedin_url": normalize_linkedin_url(linkedin_url),
        "person_slug": person_slug,
        "draft_id": draft_id,
        "wrong_notes": (wrong_notes or "").strip() or None,
        "wrong_categories": wrong_categories or [],
        "briefing_snapshot": briefing_snapshot,
        "applied_on_next_research": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    rows = _load_index()
    rows.insert(0, row)
    _save_index(rows[:500])  # keep last 500
    _dual_write_supabase(row)
    print(
        f"  [feedback] {rating} for {row['name']!r} li={row['linkedin_url']!r} "
        f"notes={bool(row['wrong_notes'])}",
        flush=True,
    )
    return row


def prior_bad_corrections(
    *,
    name: str,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    limit: int = 5,
) -> List[dict[str, Any]]:
    """Open (or recent) bad feedback for this identity — inject into synthesize."""
    li = normalize_linkedin_url(linkedin_url)
    name_l = (name or "").strip().lower()
    co_l = (company or "").strip().lower()
    out = []
    for row in _load_index():
        if row.get("rating") != "bad":
            continue
        row_li = normalize_linkedin_url(row.get("linkedin_url"))
        if li and row_li and li == row_li:
            out.append(row)
        elif name_l and (row.get("name") or "").strip().lower() == name_l:
            row_co = (row.get("company") or "").strip().lower()
            if not co_l or not row_co or co_l == row_co:
                out.append(row)
        if len(out) >= limit:
            break
    return out


def has_blocking_bad_feedback(
    *,
    name: str,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> bool:
    """If user marked research bad and hasn't been superseded by a good rating."""
    li = normalize_linkedin_url(linkedin_url)
    name_l = (name or "").strip().lower()
    co_l = (company or "").strip().lower()
    for row in _load_index():
        row_li = normalize_linkedin_url(row.get("linkedin_url"))
        match = False
        if li and row_li and li == row_li:
            match = True
        elif name_l and (row.get("name") or "").strip().lower() == name_l:
            row_co = (row.get("company") or "").strip().lower()
            if not co_l or not row_co or co_l == row_co:
                match = True
        if not match:
            continue
        if row.get("rating") == "good":
            return False  # newer good supersedes (index is newest-first)
        if row.get("rating") == "bad":
            return True
    return False


def mark_applied(feedback_ids: List[str]) -> None:
    if not feedback_ids:
        return
    want = set(feedback_ids)
    rows = _load_index()
    changed = False
    for row in rows:
        if row.get("id") in want and not row.get("applied_on_next_research"):
            row["applied_on_next_research"] = True
            changed = True
    if changed:
        _save_index(rows)


def corrections_prompt_block(rows: List[dict[str, Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "PRIOR USER CORRECTIONS for this identity (MUST honor — prefer omit over repeating mistakes):"
    ]
    for r in rows:
        cats = ", ".join(r.get("wrong_categories") or []) or "unspecified"
        notes = r.get("wrong_notes") or "(no freeform notes)"
        lines.append(f"- categories=[{cats}]; user said: {notes}")
    lines.append(
        "Do not re-introduce facts the user flagged as wrong. If unsure, leave fields empty "
        "rather than risk mixing another same-name person."
    )
    return "\n".join(lines)


def _dual_write_supabase(row: dict[str, Any]) -> None:
    try:
        from db import get_supabase

        sb = get_supabase()
        if not sb:
            return
        payload = {
            "id": row["id"],
            "user_id": row.get("user_id"),
            "person_slug": row.get("person_slug"),
            "draft_id": row.get("draft_id"),
            "name": row.get("name"),
            "company": row.get("company"),
            "linkedin_url": row.get("linkedin_url"),
            "rating": row.get("rating"),
            "wrong_notes": row.get("wrong_notes"),
            "wrong_categories": row.get("wrong_categories") or [],
            "briefing_snapshot": row.get("briefing_snapshot"),
            "applied_on_next_research": row.get("applied_on_next_research", False),
            "created_at": row.get("created_at"),
        }
        sb.table("research_feedback").upsert(payload).execute()
    except Exception as e:
        print(f"  [feedback] supabase write skipped: {e}", flush=True)
