"""Twitter/X deep fetch: simple Google "{Name} Twitter" → first link → ScrapeCreators."""

from __future__ import annotations

import os
from typing import Optional

import requests

from connectors.social_find import find_profile_link
from connectors.social_verify import verify_social_profile

SCRAPECREATORS_BASE = "https://api.scrapecreators.com"
TIMEOUT = 20


def fetch_twitter(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    twitter_url: Optional[str] = None,
    identity_hints: Optional[dict] = None,
) -> dict:
    sc_key = os.environ.get("SCRAPECREATORS_API_KEY")
    if not sc_key:
        return {"status": "skipped", "reason": "SCRAPECREATORS_API_KEY not set"}

    discovery = find_profile_link(name, "twitter", company=company, known_url=twitter_url)
    if discovery.get("status") != "ok" or not discovery.get("handle"):
        return {
            "status": "not_found",
            "discovery": discovery,
            "reason": discovery.get("reason") or "no Twitter/X profile URL from Google",
        }

    handle = discovery["handle"]
    print(f"  [twitter] ScrapeCreators profile fetch: @{handle}")
    profile_result = _fetch_profile(sc_key, handle)
    if profile_result.get("status") not in ("ok", "no_public_data"):
        return {
            **profile_result,
            "handle": handle,
            "profile_url": discovery.get("url") or f"https://x.com/{handle}",
            "discovery": discovery,
        }

    context = {
        "name": name,
        "company": company,
        "university": university,
        "place": place,
        **(identity_hints or {}),
        "platform": "twitter",
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
        "profile_url": discovery.get("url") or f"https://x.com/{handle}",
        "discovery": discovery,
        "profile": profile_result.get("profile"),
        "recent_posts": profile_result.get("recent_posts") or [],
        "match_confidence": confidence,
        "match_score": (verification or {}).get("score"),
        "match_notes": (verification or {}).get("reasons") or [],
        "verification_summary": (verification or {}).get("summary"),
    }

    if profile_result.get("status") == "no_public_data":
        return {**base, "status": "no_public_data", "reason": "Twitter/X profile not publicly readable"}

    if not is_match and verification is not None:
        return {
            **base,
            "status": "ambiguous",
            "match_notes": ((verification or {}).get("reasons") or [])
            + ((verification or {}).get("red_flags") or []),
            "reason": "Twitter/X profile found but did not verify as the same person",
        }

    if verification is None:
        base["match_confidence"] = "medium"
        base["match_notes"] = ["verifier unavailable — kept Google first-link result"]

    return {**base, "status": "ok"}


def _fetch_profile(api_key: str, handle: str) -> dict:
    try:
        resp = requests.get(
            f"{SCRAPECREATORS_BASE}/v1/twitter/profile",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            params={"handle": handle},
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
        # Some responses are flat user objects
        raw = data if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {"status": "error", "error": "unexpected ScrapeCreators response shape", "raw": data}

    user = raw.get("user") if isinstance(raw.get("user"), dict) else raw
    profile = {
        "username": user.get("screen_name") or user.get("username") or handle,
        "full_name": user.get("name") or user.get("full_name"),
        "biography": user.get("description") or user.get("biography") or user.get("bio"),
        "external_url": _expand_url(user),
        "location": user.get("location"),
        "followers": user.get("followers_count") or user.get("followers") or user.get("normal_followers_count"),
        "following": user.get("friends_count") or user.get("following"),
        "statuses_count": user.get("statuses_count") or user.get("tweet_count"),
        "is_verified": user.get("verified") or user.get("is_blue_verified") or user.get("is_verified"),
        "profile_pic_url": user.get("profile_image_url_https") or user.get("profile_image_url"),
    }
    posts = []
    for key in ("tweets", "recent_tweets", "posts", "status"):
        val = raw.get(key)
        if isinstance(val, list):
            for item in val[:8]:
                if isinstance(item, dict):
                    posts.append({
                        "caption": (item.get("full_text") or item.get("text") or item.get("caption") or "")[:400] or None,
                        "timestamp": item.get("created_at") or item.get("timestamp"),
                        "likes": item.get("favorite_count") or item.get("likes"),
                        "url": item.get("url"),
                    })
            break
    return {"status": "ok", "profile": profile, "recent_posts": posts}


def _expand_url(user: dict) -> Optional[str]:
    url_obj = user.get("url")
    if isinstance(url_obj, str):
        return url_obj
    entities = user.get("entities") or {}
    url_ents = (entities.get("url") or {}).get("urls") if isinstance(entities.get("url"), dict) else None
    if isinstance(url_ents, list) and url_ents:
        return url_ents[0].get("expanded_url") or url_ents[0].get("url")
    return user.get("website") or user.get("external_url")
