"""Facebook deep fetch: simple Google "{Name} Facebook" → first link → ScrapeCreators."""

from __future__ import annotations

import os
from typing import Optional

import requests

from connectors.social_find import find_profile_link
from connectors.social_verify import verify_social_profile

SCRAPECREATORS_BASE = "https://api.scrapecreators.com"
TIMEOUT = 20


def fetch_facebook(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    facebook_url: Optional[str] = None,
    identity_hints: Optional[dict] = None,
) -> dict:
    sc_key = os.environ.get("SCRAPECREATORS_API_KEY")
    if not sc_key:
        return {"status": "skipped", "reason": "SCRAPECREATORS_API_KEY not set"}

    discovery = find_profile_link(name, "facebook", company=company, known_url=facebook_url)
    if discovery.get("status") != "ok" or not discovery.get("url"):
        return {
            "status": "not_found",
            "discovery": discovery,
            "reason": discovery.get("reason") or "no Facebook profile URL from Google",
        }

    url = discovery["url"]
    print(f"  [facebook] ScrapeCreators profile fetch: {url}")
    profile_result = _fetch_profile(sc_key, url)
    if profile_result.get("status") not in ("ok", "no_public_data"):
        return {**profile_result, "profile_url": url, "discovery": discovery}

    context = {
        "name": name,
        "company": company,
        "university": university,
        "place": place,
        **(identity_hints or {}),
        "platform": "facebook",
    }
    verification = verify_social_profile(context, {
        "handle": discovery.get("handle"),
        "url": url,
        "profile": profile_result.get("profile"),
        "recent_posts": profile_result.get("recent_posts") or [],
        "fetch_status": profile_result.get("status"),
    })

    confidence = (verification or {}).get("confidence") or "low"
    is_match = (verification or {}).get("match") is True and confidence in ("high", "medium")

    base = {
        "handle": discovery.get("handle"),
        "profile_url": url,
        "discovery": discovery,
        "profile": profile_result.get("profile"),
        "recent_posts": profile_result.get("recent_posts") or [],
        "match_confidence": confidence,
        "match_score": (verification or {}).get("score"),
        "match_notes": (verification or {}).get("reasons") or [],
        "verification_summary": (verification or {}).get("summary"),
    }

    if profile_result.get("status") == "no_public_data":
        return {**base, "status": "no_public_data", "reason": "Facebook profile not publicly readable"}

    if not is_match and verification is not None:
        return {
            **base,
            "status": "ambiguous",
            "match_notes": ((verification or {}).get("reasons") or [])
            + ((verification or {}).get("red_flags") or []),
            "reason": "Facebook profile found but did not verify as the same person",
        }

    if verification is None:
        base["match_confidence"] = "medium"
        base["match_notes"] = ["verifier unavailable — kept Google first-link result"]

    return {**base, "status": "ok"}


def _fetch_profile(api_key: str, url: str) -> dict:
    try:
        resp = requests.get(
            f"{SCRAPECREATORS_BASE}/v1/facebook/profile",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            params={"url": url},
            timeout=TIMEOUT,
        )
        if resp.status_code == 404:
            return {"status": "not_found", "reason": "Facebook profile not found"}
        if resp.status_code == 401:
            return {"status": "error", "error": "SCRAPECREATORS_API_KEY rejected (401)"}
        if resp.status_code >= 400:
            return {"status": "error", "error": f"ScrapeCreators HTTP {resp.status_code}: {resp.text[:300]}"}
        data = resp.json()
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)}
    except ValueError as exc:
        return {"status": "error", "error": f"invalid JSON: {exc}"}

    raw = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if not isinstance(raw, dict):
        return {"status": "error", "error": "unexpected ScrapeCreators response shape", "raw": data}

    profile = {
        "name": raw.get("name") or raw.get("full_name") or raw.get("title"),
        "biography": raw.get("about") or raw.get("biography") or raw.get("bio") or raw.get("description"),
        "website": raw.get("website") or raw.get("external_url") or raw.get("url"),
        "location": raw.get("location") or raw.get("city"),
        "followers": raw.get("followers") or raw.get("follower_count") or raw.get("likes"),
        "category": raw.get("category") or raw.get("category_name"),
        "is_verified": raw.get("is_verified") or raw.get("verified"),
    }
    posts = []
    for key in ("posts", "recent_posts", "feed"):
        val = raw.get(key)
        if isinstance(val, list):
            for item in val[:8]:
                if isinstance(item, dict):
                    posts.append({
                        "caption": (item.get("text") or item.get("message") or item.get("caption") or "")[:400] or None,
                        "timestamp": item.get("created_time") or item.get("timestamp") or item.get("date"),
                        "url": item.get("url") or item.get("permalink"),
                    })
            break
    return {"status": "ok", "profile": profile, "recent_posts": posts}
