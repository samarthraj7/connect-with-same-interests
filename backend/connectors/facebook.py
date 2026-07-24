"""Facebook deep fetch: Google candidates → multi-signal rank → ScrapeCreators."""

from __future__ import annotations

import os
from typing import List, Optional

import requests

from connectors.social_find import (
    find_profile_candidates,
    find_profile_link,
    pick_ranked_profile,
    rank_profile_candidates,
)
from connectors.social_verify import verify_social_profile

SCRAPECREATORS_BASE = "https://api.scrapecreators.com"
TIMEOUT = 20
MAX_RANK_CANDIDATES = 4


def fetch_facebook(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    facebook_url: Optional[str] = None,
    identity_hints: Optional[dict] = None,
    search_constraints: Optional[dict] = None,
) -> dict:
    sc_key = os.environ.get("SCRAPECREATORS_API_KEY")
    if not sc_key:
        return {"status": "skipped", "reason": "SCRAPECREATORS_API_KEY not set"}

    hints = identity_hints or {}
    hint = None
    if search_constraints and isinstance(search_constraints, dict):
        hint = (search_constraints.get("distinguishable_factor") or "").strip() or None
    if not hint:
        hint = (hints.get("distinguishable_factor") or hints.get("hint") or "").strip() or None

    discovery = find_profile_candidates(
        name,
        "facebook",
        company=company,
        university=university,
        distinguishable_factor=hint,
        known_url=facebook_url,
        max_candidates=MAX_RANK_CANDIDATES,
        search_constraints=search_constraints,
    )
    cands = list(discovery.get("candidates") or [])
    if not cands:
        # Legacy single-path fallback
        single = find_profile_link(name, "facebook", company=company, known_url=facebook_url)
        if single.get("status") == "ok" and single.get("url"):
            cands = [
                {
                    "url": single.get("url"),
                    "handle": single.get("handle"),
                    "method": single.get("method"),
                    "title": name,
                }
            ]
            discovery = single
        else:
            return {
                "status": "not_found",
                "discovery": discovery,
                "reason": discovery.get("reason") or "no Facebook profile URL from Google",
            }

    # Fetch a few candidates when we have disambiguation hints / multiple hits
    fetch_n = MAX_RANK_CANDIDATES if (hint or company or university or len(cands) > 1) else 1
    profiles: List[dict] = []
    for c in cands[:fetch_n]:
        url = c.get("url")
        if not url:
            continue
        print(f"  [facebook] ScrapeCreators profile fetch: {url}")
        profile_result = _fetch_profile(sc_key, url)
        if profile_result.get("status") not in ("ok", "no_public_data"):
            continue
        handle = c.get("handle") or url
        profiles.append(
            {
                "handle": handle,
                "profile_url": url,
                "profile": profile_result.get("profile"),
                "recent_posts": profile_result.get("recent_posts") or [],
                "fetch_status": profile_result.get("status"),
                "discovery_cand": c,
            }
        )

    if not profiles:
        top = cands[0]
        url = top.get("url")
        profile_result = _fetch_profile(sc_key, url) if url else {"status": "not_found"}
        if profile_result.get("status") not in ("ok", "no_public_data"):
            return {**profile_result, "profile_url": url, "discovery": discovery}
        profiles = [
            {
                "handle": top.get("handle") or url,
                "profile_url": url,
                "profile": profile_result.get("profile"),
                "recent_posts": profile_result.get("recent_posts") or [],
                "fetch_status": profile_result.get("status"),
            }
        ]

    chosen = profiles[0]
    ranked = None
    if len(profiles) > 1 or hint or company or university:
        linkedin_slug = (hints.get("linkedin_slug") or "").strip() or None
        ranked = rank_profile_candidates(
            profiles,
            name=name,
            company=company,
            university=university,
            place=place,
            hint=hint,
            linkedin_slug=linkedin_slug,
        )
        for row in (ranked or [])[:6]:
            print(
                f"  [facebook] variety {row['handle']} total={row['identity_score']:.1f} "
                f"signals={row.get('signals')}",
                flush=True,
            )
        picked = pick_ranked_profile(ranked, profiles)
        if picked:
            chosen = picked
            print(
                f"  [facebook] multi-signal pick {chosen.get('handle')} "
                f"(score={(ranked[0] or {}).get('identity_score')})",
                flush=True,
            )
        elif len(profiles) > 1 and ranked:
            # No clear winner among multiples — fall through to verify top-ranked
            chosen = next(
                (
                    p
                    for p in profiles
                    if (p.get("handle") or "").lstrip("@").lower()
                    == (ranked[0].get("handle") or "").lower()
                ),
                profiles[0],
            )

    url = chosen.get("profile_url")
    context = {
        "name": name,
        "company": company,
        "university": university,
        "place": place,
        **hints,
        "platform": "facebook",
    }
    verification = verify_social_profile(
        context,
        {
            "handle": chosen.get("handle"),
            "url": url,
            "profile": chosen.get("profile"),
            "recent_posts": chosen.get("recent_posts") or [],
            "fetch_status": chosen.get("fetch_status"),
        },
    )

    confidence = (verification or {}).get("confidence") or "low"
    is_match = (verification or {}).get("match") is True and confidence in ("high", "medium")

    base = {
        "handle": chosen.get("handle"),
        "profile_url": url,
        "discovery": discovery,
        "profile": chosen.get("profile"),
        "recent_posts": chosen.get("recent_posts") or [],
        "match_confidence": confidence,
        "match_score": (verification or {}).get("score"),
        "match_notes": (verification or {}).get("reasons") or [],
        "verification_summary": (verification or {}).get("summary"),
        "identity_rankings": ranked[:6] if ranked else None,
    }

    if chosen.get("fetch_status") == "no_public_data":
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
        base["match_notes"] = ["verifier unavailable — kept ranked Google result"]

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
