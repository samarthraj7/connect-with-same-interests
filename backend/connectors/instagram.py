"""Instagram deep fetch: simple Google "{Name} Instagram" → first link → ScrapeCreators."""

from __future__ import annotations

import os
from typing import List, Optional

import requests

from connectors.social_find import find_profile_link
from connectors.social_verify import verify_social_profile

SCRAPECREATORS_BASE = "https://api.scrapecreators.com"
APIDIRECT_POSTS = "https://apidirect.io/v1/instagram/posts"
TIMEOUT = 20


def fetch_instagram(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    instagram_url: Optional[str] = None,
    candidate_urls: Optional[List[str]] = None,  # kept for orchestrator compat; unused
    identity_hints: Optional[dict] = None,
) -> dict:
    sc_key = os.environ.get("SCRAPECREATORS_API_KEY")
    if not sc_key:
        return {"status": "skipped", "reason": "SCRAPECREATORS_API_KEY not set"}

    discovery = find_profile_link(name, "instagram", company=company, known_url=instagram_url)
    if discovery.get("status") != "ok" or not discovery.get("handle"):
        return {
            "status": "not_found",
            "discovery": discovery,
            "reason": discovery.get("reason") or "no Instagram profile URL from Google",
        }

    handle = discovery["handle"]
    print(f"  [instagram] ScrapeCreators profile fetch: @{handle}")
    profile_result = _fetch_profile(sc_key, handle)
    if profile_result.get("status") not in ("ok", "no_public_data"):
        return {
            **profile_result,
            "handle": handle,
            "profile_url": discovery.get("url") or f"https://www.instagram.com/{handle}/",
            "discovery": discovery,
        }

    context = {
        "name": name,
        "company": company,
        "university": university,
        "place": place,
        **(identity_hints or {}),
        "platform": "instagram",
    }
    verification = verify_social_profile(context, {
        "handle": handle,
        "url": discovery.get("url"),
        "profile": profile_result.get("profile"),
        "recent_posts": profile_result.get("recent_posts") or [],
        "fetch_status": profile_result.get("status"),
    })

    confidence = (verification or {}).get("confidence") or "low"
    is_match = (verification or {}).get("match") is True and confidence in ("high", "medium")

    base = {
        "handle": handle,
        "profile_url": discovery.get("url") or f"https://www.instagram.com/{handle}/",
        "discovery": discovery,
        "profile": profile_result.get("profile"),
        "recent_posts": profile_result.get("recent_posts") or [],
        "match_confidence": confidence,
        "match_score": (verification or {}).get("score"),
        "match_notes": (verification or {}).get("reasons") or [],
        "verification_summary": (verification or {}).get("summary"),
    }

    if profile_result.get("status") == "no_public_data":
        return {**base, "status": "no_public_data", "reason": "Instagram account is private"}

    if not is_match and verification is not None:
        return {
            **base,
            "status": "ambiguous",
            "match_notes": ((verification or {}).get("reasons") or [])
            + ((verification or {}).get("red_flags") or []),
            "reason": "Instagram profile found but did not verify as the same person",
        }

    # If verifier unavailable, still return ok when Google found the link (user's preferred path)
    if verification is None:
        base["match_confidence"] = "medium"
        base["match_notes"] = ["verifier unavailable — kept Google first-link result"]

    posts = _fetch_posts_apidirect(handle, name)
    result = {**base, "status": "ok"}
    if posts is not None:
        result["apidirect_posts"] = posts
    return result


def _fetch_profile(api_key: str, handle: str) -> dict:
    try:
        resp = requests.get(
            f"{SCRAPECREATORS_BASE}/v1/instagram/profile",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            params={"handle": handle, "trim": "true"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 404:
            return {"status": "not_found", "reason": f"profile @{handle} not found"}
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

    user = raw.get("user") if isinstance(raw.get("user"), dict) else raw
    profile = {
        "username": user.get("username") or handle,
        "full_name": user.get("full_name") or user.get("fullname"),
        "biography": user.get("biography") or user.get("bio"),
        "external_url": user.get("external_url") or user.get("website"),
        "followers": user.get("follower_count") or user.get("followers"),
        "following": user.get("following_count") or user.get("following"),
        "media_count": user.get("media_count"),
        "is_private": user.get("is_private"),
        "is_verified": user.get("is_verified"),
        "category_name": user.get("category_name"),
        "profile_pic_url": user.get("profile_pic_url") or user.get("profile_pic_url_hd"),
    }
    if profile.get("is_private") is True:
        return {"status": "no_public_data", "profile": profile, "recent_posts": []}

    posts = []
    for key in ("recent_posts", "posts", "medias", "items"):
        val = raw.get(key)
        if isinstance(val, list):
            for item in val[:8]:
                if not isinstance(item, dict):
                    continue
                caption = item.get("caption") or item.get("text")
                if isinstance(caption, dict):
                    caption = caption.get("text")
                posts.append({
                    "id": item.get("id") or item.get("pk") or item.get("code"),
                    "caption": (caption or "")[:400] or None,
                    "like_count": item.get("like_count") or item.get("likes"),
                    "timestamp": item.get("taken_at") or item.get("timestamp") or item.get("date"),
                })
            break
    return {"status": "ok", "profile": profile, "recent_posts": posts}


def _fetch_posts_apidirect(handle: str, name: str) -> Optional[dict]:
    api_key = os.environ.get("APIDIRECT_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.get(
            APIDIRECT_POSTS,
            headers={"X-API-Key": api_key},
            params={"query": handle, "pages": 1},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            return {"status": "error", "error": f"APIDirect HTTP {resp.status_code}"}
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"status": "error", "error": str(exc)}

    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list):
        return {"status": "error", "error": "unexpected APIDirect response"}
    handle_l = handle.lower()
    own = [p for p in posts if (p.get("author") or "").lower() == handle_l]
    trimmed = [
        {
            "url": p.get("url"),
            "date": p.get("date"),
            "author": p.get("author"),
            "snippet": (p.get("snippet") or "")[:400] or None,
            "likes": p.get("likes"),
        }
        for p in (own or posts)[:10]
    ]
    return {"status": "ok", "matched_author": bool(own), "posts": trimmed}
