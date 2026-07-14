"""Verify user-supplied social handles against claimed identity."""

from __future__ import annotations

from typing import Any, Optional

from connectors import github, social_verify
from connectors import instagram, twitter


def verify_handles(
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    handles: dict[str, str],
) -> dict[str, Any]:
    """Return per-handle status: verified | ambiguous | rejected | skipped."""
    target = {
        "name": name,
        "company": company,
        "university": university,
    }
    results: dict[str, Any] = {}

    if handles.get("github"):
        results["github"] = _verify_github(handles["github"], target)
    if handles.get("linkedin"):
        results["linkedin"] = _verify_linkedin_url(handles["linkedin"], target)
    if handles.get("instagram"):
        results["instagram"] = _verify_instagram(handles["instagram"], target)
    if handles.get("twitter"):
        results["twitter"] = _verify_twitter(handles["twitter"], target)

    return {"status": "ok", "results": results}


def _status_from_match(match: Optional[dict]) -> str:
    if not match:
        return "ambiguous"
    if match.get("match") and (match.get("confidence") in ("high", "medium") or (match.get("score") or 0) >= 0.65):
        return "verified"
    if match.get("match") is False and (match.get("confidence") == "high" or (match.get("score") or 0) < 0.35):
        return "rejected"
    return "ambiguous"


def _verify_github(username: str, target: dict) -> dict:
    username = username.strip().lstrip("@")
    data = github.search_github(name=target["name"], username=username, company=target.get("company"))
    if data.get("status") != "ok":
        return {"status": "ambiguous", "summary": data.get("error") or data.get("status"), "raw": data}
    # If exact username fetch worked, treat as verified when profile name loosely matches
    profile = data.get("profile") or data.get("user") or data
    display = (profile.get("name") or profile.get("login") or "").lower()
    claimed = (target.get("name") or "").lower()
    parts = claimed.split()
    ok = any(p and p in display for p in parts) or username.lower() in claimed.replace(" ", "")
    status = "verified" if ok else "ambiguous"
    return {
        "status": status,
        "summary": f"GitHub @{username}" + (" matches name" if ok else " — name unclear"),
        "url": profile.get("html_url") or f"https://github.com/{username}",
    }


def _verify_linkedin_url(url: str, target: dict) -> dict:
    url = url.strip()
    if "linkedin.com" not in url.lower():
        return {"status": "rejected", "summary": "Not a LinkedIn URL"}
    # Lightweight structural verify; deep scrape is optional/flagged
    slug = url.rstrip("/").split("/")[-1]
    name_bits = [p for p in (target.get("name") or "").lower().split() if len(p) > 2]
    slug_l = slug.lower().replace("-", "")
    hit = any(p in slug_l for p in name_bits)
    return {
        "status": "verified" if hit else "ambiguous",
        "summary": "LinkedIn URL name slug check",
        "url": url,
    }


def _verify_instagram(handle: str, target: dict) -> dict:
    handle = handle.strip().lstrip("@")
    data = instagram.fetch_instagram(
        name=target["name"],
        company=target.get("company"),
        university=target.get("university"),
        instagram_url=f"https://www.instagram.com/{handle}/",
        identity_hints={"current_role": target.get("company")},
    )
    if data.get("status") not in ("ok", "partial"):
        return {"status": "ambiguous", "summary": data.get("status") or "fetch failed"}
    match = data.get("identity_match") or social_verify.verify_social_profile(target, data)
    status = _status_from_match(match if isinstance(match, dict) else None)
    return {"status": status, "summary": (match or {}).get("summary") if isinstance(match, dict) else status, "handle": handle}


def _verify_twitter(handle: str, target: dict) -> dict:
    handle = handle.strip().lstrip("@")
    data = twitter.fetch_twitter(
        name=target["name"],
        company=target.get("company"),
        university=target.get("university"),
        twitter_url=f"https://x.com/{handle}",
        identity_hints={"current_role": target.get("company")},
    )
    if data.get("status") not in ("ok", "partial"):
        return {"status": "ambiguous", "summary": data.get("status") or "fetch failed"}
    match = data.get("identity_match") or social_verify.verify_social_profile(target, data)
    status = _status_from_match(match if isinstance(match, dict) else None)
    return {"status": status, "summary": (match or {}).get("summary") if isinstance(match, dict) else status, "handle": handle}
