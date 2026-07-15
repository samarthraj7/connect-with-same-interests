"""Heuristics for thin public footprints (students, early-career, private people)."""

from __future__ import annotations

from typing import Any, List, Optional


def briefing_density(summary: dict[str, Any]) -> str:
    """Return 'rich' | 'ok' | 'sparse' based on how much public signal we have."""
    if not isinstance(summary, dict) or summary.get("status") not in (None, "ok"):
        # status may be absent on OK summaries
        pass
    career = summary.get("career_history") or []
    interests = summary.get("interests") or []
    notable = summary.get("notable_points") or []
    affiliations = summary.get("notable_affiliations") or []
    personal = summary.get("personal_info") if isinstance(summary.get("personal_info"), dict) else {}
    presence = summary.get("public_presence") if isinstance(summary.get("public_presence"), dict) else {}
    conf = (summary.get("identity_confidence") or "").lower()

    score = 0
    if len(career) >= 4:
        score += 3
    elif len(career) >= 2:
        score += 2
    elif len(career) >= 1:
        score += 1
    if interests:
        score += 1
    if notable:
        score += 1
    if affiliations:
        score += 1
    if personal.get("hobbies") or personal.get("sports_interests"):
        score += 1
    if personal.get("current_location") or personal.get("raised_in"):
        score += 1
    if (presence.get("posts_about") or presence.get("recent_posts_or_writing")):
        score += 1
    if conf == "high":
        score += 1
    elif conf in ("low", "unverified"):
        score -= 1

    if score >= 7:
        return "rich"
    if score >= 3:
        return "ok"
    return "sparse"


def sparse_recovery_suggestions(name: str, company: Optional[str] = None) -> List[str]:
    """What to try next when public web research is thin."""
    who = f"{name}" + (f" @ {company}" if company else "")
    return [
        f"Ask for {who}'s LinkedIn URL (best single unlock).",
        "Ask for university / graduation year — then search alumni + campus mentions.",
        "Ask for Instagram / GitHub / personal site handles if you have them.",
        "Add anything you already know (club, project, mutual friend, hometown).",
        "If they join Connect Deeply later, their signup research fills the gaps automatically.",
    ]
