"""Load the searcher's living profile used for common-ground analysis.

Edit ``user_profile.json`` in this directory. Accepted formats:
1) Flat "YOU" profile (name, interests, career_highlights, …)
2) A full research dump from ``profiles/*.json`` (latest_summary + sources)
   — automatically normalized into the flat shape.

Phase 3 will refine this profile over time from saved interactions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

USER_PROFILE_PATH = Path(__file__).parent / "user_profile.json"

# Fields the overlap model cares about most (kept small for the prompt).
_OVERLAP_KEYS = (
    "name",
    "headline",
    "current_role",
    "current_company",
    "location",
    "hometown_or_raised",
    "lived_in",
    "education",
    "career_highlights",
    "industries",
    "skills_and_expertise",
    "interests",
    "hobbies",
    "sports",
    "causes_and_affiliations",
    "languages",
    "talking_goals",
    "avoid_topics",
)


def default_path() -> Path:
    override = os.environ.get("CONNECT_DEEPLY_USER_PROFILE")
    if override:
        return Path(override)
    return USER_PROFILE_PATH


def load_user_profile(path: Optional[Path] = None) -> dict[str, Any]:
    profile_path = path or default_path()
    if not profile_path.exists():
        raise FileNotFoundError(
            f"User profile not found at {profile_path}. "
            "Copy/edit user_profile.json with your details."
        )
    data = json.loads(profile_path.read_text())
    if not isinstance(data, dict):
        raise ValueError("user_profile.json must be a JSON object")
    return normalize_user_profile(data)


def normalize_user_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Accept flat YOU profiles or research dumps from profiles/*.json."""
    if _looks_like_research_dump(data):
        return _from_research_profile(data)
    return data


def _looks_like_research_dump(data: dict[str, Any]) -> bool:
    return bool(data.get("latest_summary") or data.get("latest_sources"))


def _from_research_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Map a stored research JSON into the flat YOU schema for overlap."""
    summary = data.get("latest_summary") if isinstance(data.get("latest_summary"), dict) else {}
    sources = data.get("latest_sources") if isinstance(data.get("latest_sources"), dict) else {}
    gemini = sources.get("gemini_search") if isinstance(sources.get("gemini_search"), dict) else {}
    personal = {}
    if isinstance(summary.get("personal_info"), dict):
        personal = summary["personal_info"]
    elif isinstance(sources.get("personal_info"), dict):
        personal = sources["personal_info"]

    career = summary.get("career_history") or gemini.get("career_history") or []
    education = gemini.get("education") or []
    if isinstance(education, list):
        # De-dupe while preserving order
        seen = set()
        education = [e for e in education if not (e in seen or seen.add(e))]

    location = (
        personal.get("current_location")
        or data.get("place")
        or data.get("location")
    )
    hobbies = personal.get("hobbies") or []
    sports = personal.get("sports_interests") or personal.get("sports") or []
    lived_in = personal.get("lived_in") or []
    hometown = personal.get("born_or_hometown") or personal.get("raised_in") or ""

    interests = list(summary.get("interests") or [])
    # Fold hobbies into interests only as extras for overlap signal
    for h in hobbies:
        if h and h not in interests:
            interests.append(h)

    affiliations = list(summary.get("notable_affiliations") or [])
    if data.get("company") and data["company"] not in affiliations:
        affiliations.insert(0, data["company"])

    contact = data.get("contact") if isinstance(data.get("contact"), dict) else {}
    if not contact.get("linkedin_url"):
        for key in ("exa_search", "gemini_search", "linkedin_public"):
            src = sources.get(key) or {}
            url = src.get("linkedin_url") or (src.get("profile") or {}).get("url")
            if url:
                contact = {**contact, "linkedin_url": url}
                break

    headline_bits = [
        gemini.get("current_role") or summary.get("current_role"),
        gemini.get("current_company") or summary.get("current_company") or data.get("company"),
    ]
    headline = " · ".join(str(b) for b in headline_bits if b)

    out: dict[str, Any] = {
        "name": data.get("name") or "",
        "headline": headline,
        "current_role": gemini.get("current_role") or "",
        "current_company": gemini.get("current_company") or data.get("company") or "",
        "location": location or "",
        "hometown_or_raised": hometown or "",
        "lived_in": _cap_list(lived_in, 12),
        "education": _cap_list(education, 8),
        "career_highlights": _cap_list(career, 16),
        "industries": [],
        "skills_and_expertise": [],
        "interests": _cap_list(interests, 16),
        "hobbies": _cap_list(hobbies, 12),
        "sports": _cap_list(sports, 12),
        "causes_and_affiliations": _cap_list(affiliations, 16),
        "languages": [],
        "talking_goals": [
            "Find genuine common ground before meetings",
            "Open warm, specific conversations",
        ],
        "avoid_topics": [],
        "contact": contact,
        "crm": {
            "notes": "Normalized from a research profile dump (profiles/*.json).",
            "source": "research_dump",
        },
        "summary_blurb": (summary.get("summary") or gemini.get("bio_summary") or "")[:1200],
        "notable_points": _cap_list(summary.get("notable_points") or [], 10),
    }
    return out


def _cap_list(items: Any, limit: int) -> list:
    if not isinstance(items, list):
        return []
    out = []
    seen = set()
    for item in items:
        if item in (None, ""):
            continue
        key = item if not isinstance(item, (dict, list)) else json.dumps(item, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def profile_from_research(
    *,
    name: str,
    company: Optional[str],
    summary: dict[str, Any],
    sources: Optional[dict[str, Any]] = None,
    contact: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a YOU profile from a research summary (signup self-research)."""
    dump = {
        "name": name,
        "company": company,
        "latest_summary": summary,
        "latest_sources": sources or {},
        "contact": contact or {},
    }
    flat = _from_research_profile(dump)
    flat["crm"] = {
        "notes": "Built from public research at signup.",
        "source": "researched_at_signup",
    }
    flat["profile_source"] = "researched_at_signup"
    flat["researched_at"] = None  # filled by caller
    return flat


def merge_manual_overlays(base: dict[str, Any], overlays: dict[str, Any]) -> dict[str, Any]:
    """Layer optional signup extras onto a researched profile without wiping research."""
    out = dict(base)
    # Scalar overlays only if research left them empty
    for key in ("headline", "location", "hometown_or_raised", "current_company"):
        if overlays.get(key) and not out.get(key):
            out[key] = overlays[key]
    # List fields: append unique manual items (user-supplied hobbies etc.)
    for key in (
        "hobbies",
        "interests",
        "sports",
        "education",
        "career_highlights",
        "causes_and_affiliations",
        "talking_goals",
        "avoid_topics",
    ):
        manual = overlays.get(key) or []
        if not isinstance(manual, list):
            continue
        existing = list(out.get(key) or [])
        for item in manual:
            if item and item not in existing:
                existing.append(item)
        if existing:
            out[key] = existing
    # Contact merge
    base_contact = dict(out.get("contact") or {})
    over_contact = overlays.get("contact") if isinstance(overlays.get("contact"), dict) else {}
    for k, v in over_contact.items():
        if v and not base_contact.get(k):
            base_contact[k] = v
    out["contact"] = base_contact
    return out


def profile_for_overlap(profile: dict[str, Any]) -> dict[str, Any]:
    """Strip CRM/meta noise; keep only fields useful for overlap reasoning."""
    # Ensure research dumps are normalized even if caller bypassed load_user_profile.
    if _looks_like_research_dump(profile):
        profile = _from_research_profile(profile)

    slim = {k: profile.get(k) for k in _OVERLAP_KEYS if profile.get(k) not in (None, "", [])}
    # Extra signal from normalized research dumps
    for extra in ("summary_blurb", "notable_points"):
        if profile.get(extra):
            slim[extra] = profile[extra]
    contact = profile.get("contact") or {}
    if isinstance(contact, dict) and contact.get("linkedin_url"):
        slim["linkedin_url"] = contact["linkedin_url"]
    return slim


def is_usable(profile: dict[str, Any]) -> bool:
    """True when there is enough 'you' signal to run common-ground analysis."""
    slim = profile_for_overlap(profile)
    return bool(slim.get("name") and len(slim) >= 3)
