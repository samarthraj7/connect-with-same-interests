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
    linkedin_url: Optional[str] = None,
    handles: dict[str, str],
) -> dict[str, Any]:
    """Return per-handle status: verified | ambiguous | rejected | skipped."""
    target = {
        "name": name,
        "company": company,
        "university": university,
        "linkedin_url": linkedin_url or handles.get("linkedin"),
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
    linkedin_url = (target.get("linkedin_url") or "").strip() or None
    data = github.search_github(
        name=target["name"],
        username=username,
        company=target.get("company"),
        linkedin_url=linkedin_url,
    )
    profile = data.get("profile") or {}
    url = profile.get("html_url") or f"https://github.com/{username}"
    im = data.get("identity_match")

    if not im and linkedin_url:
        try:
            from identity_resolve import public_resolution, resolve_linkedin_github

            im = public_resolution(
                resolve_linkedin_github(
                    linkedin_url=linkedin_url,
                    github_username=username,
                    name=target.get("name"),
                    company=target.get("company"),
                )
            )
        except Exception as exc:
            return {
                "status": "ambiguous",
                "summary": f"identity_resolve error: {exc}",
                "url": url,
                "raw": data,
            }

    if im:
        tier = im.get("tier")
        if tier == "confirmed":
            status = "verified"
        elif tier == "possible":
            status = "ambiguous"
        else:
            status = "rejected"
        return {
            "status": status,
            "summary": f"GitHub @{username} · {tier} (score={im.get('score')})",
            "url": url,
            "identity_match": im,
            "evidence": im.get("evidence") or [],
        }

    # No LinkedIn to resolve against — never "verified" on name alone
    if data.get("status") not in ("ok", "ambiguous"):
        return {"status": "ambiguous", "summary": data.get("error") or data.get("status"), "raw": data}
    return {
        "status": "ambiguous",
        "summary": f"GitHub @{username} fetched — LinkedIn required to confirm same person",
        "url": url,
        "identity_match": {
            "linkedin_url": None,
            "candidate_url": url,
            "score": 0.0,
            "tier": "no_match",
            "evidence": ["No LinkedIn URL provided for corroboration"],
        },
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
