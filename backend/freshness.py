"""Incremental freshness: fingerprints, what's-new diffs, pending facts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional


def fingerprint_payload(payload: Any) -> str:
    """Stable short hash of settled source payload (ignore volatile timestamps)."""
    try:
        blob = json.dumps(payload, sort_keys=True, default=str)
    except TypeError:
        blob = str(payload)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def source_fingerprints(sources: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in (sources or {}).items():
        if not isinstance(v, dict):
            continue
        if v.get("status") not in ("ok", "not_found", "no_public_data", "blocked"):
            continue
        # Drop nested fetch timestamps if present
        slim = {kk: vv for kk, vv in v.items() if kk not in ("fetched_at", "raw_response")}
        out[k] = fingerprint_payload(slim)
    out["_all"] = fingerprint_payload(sorted(out.items()))
    return out


def diff_whats_new(
    old_fps: Optional[dict[str, str]],
    new_fps: dict[str, str],
    *,
    old_summary: Optional[dict] = None,
    new_summary: Optional[dict] = None,
) -> list[dict[str, Any]]:
    """Human-readable change list for the person page."""
    old_fps = old_fps or {}
    changes: list[dict[str, Any]] = []
    for source, fp in new_fps.items():
        if source.startswith("_"):
            continue
        prev = old_fps.get(source)
        if prev is None:
            changes.append({"type": "source_added", "source": source, "detail": "New source data"})
        elif prev != fp:
            changes.append({"type": "source_updated", "source": source, "detail": "Updated since last fetch"})
    for source in old_fps:
        if source.startswith("_"):
            continue
        if source not in new_fps:
            changes.append({"type": "source_missing", "source": source, "detail": "No longer present"})

    # Light summary field diffs
    if isinstance(old_summary, dict) and isinstance(new_summary, dict):
        for key in ("headline", "summary", "current_role"):
            a = old_summary.get(key)
            b = new_summary.get(key)
            if a and b and a != b:
                changes.append({"type": "summary_field", "field": key, "detail": f"{key} changed"})
        old_posts = _recent_signal(old_summary)
        new_posts = _recent_signal(new_summary)
        if new_posts and new_posts != old_posts:
            changes.append(
                {
                    "type": "activity",
                    "detail": "New public activity or writing signal",
                    "snippet": new_posts[:180],
                }
            )
    return changes


def _recent_signal(summary: dict) -> str:
    for key in ("recent_activity", "notable", "career_highlights"):
        val = summary.get(key)
        if isinstance(val, list) and val:
            return str(val[0])
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def attach_fingerprints_to_record(record: dict, sources: dict) -> dict:
    fps = source_fingerprints(sources)
    record["content_fingerprints"] = fps
    record["fingerprints_at"] = datetime.now(timezone.utc).isoformat()
    return record


def compute_whats_new(existing: Optional[dict], new_sources: dict, new_summary: dict) -> list[dict]:
    old_fps = (existing or {}).get("content_fingerprints") or {}
    cached = dict((existing or {}).get("latest_sources") or {})
    cached.update(new_sources or {})
    new_fps = source_fingerprints(cached)
    old_summary = (existing or {}).get("latest_summary")
    changes = diff_whats_new(old_fps, new_fps, old_summary=old_summary, new_summary=new_summary)
    return changes
