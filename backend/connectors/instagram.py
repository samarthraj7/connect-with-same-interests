"""Instagram deep fetch: multi-candidate Google → ScrapeCreators → face match vs LinkedIn photo."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests

from connectors.social_find import find_profile_candidates, find_profile_link
from connectors.social_verify import verify_social_profile

SCRAPECREATORS_BASE = "https://api.scrapecreators.com"
APIDIRECT_POSTS = "https://apidirect.io/v1/instagram/posts"
TIMEOUT = 20
MAX_FACE_CANDIDATES = int(os.environ.get("IG_FACE_MATCH_MAX") or "6")


def fetch_instagram(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    instagram_url: Optional[str] = None,
    candidate_urls: Optional[List[str]] = None,
    identity_hints: Optional[dict] = None,
) -> dict:
    sc_key = os.environ.get("SCRAPECREATORS_API_KEY")
    if not sc_key:
        return {"status": "skipped", "reason": "SCRAPECREATORS_API_KEY not set"}

    hints = identity_hints or {}
    ref_photo = (hints.get("photo_url") or hints.get("linkedin_photo_url") or "").strip()

    discovery = find_profile_candidates(
        name,
        "instagram",
        company=company,
        known_url=instagram_url,
        max_candidates=10,
    )
    # Merge any orchestrator-provided URLs
    extra = []
    for u in candidate_urls or []:
        if u:
            extra.append({"url": u, "handle": None, "method": "seed_url"})
    cands = list(discovery.get("candidates") or []) + extra
    # Dedupe by handle
    seen = set()
    uniq = []
    for c in cands:
        h = (c.get("handle") or "").lower()
        if not h and c.get("url"):
            from connectors.social_find import _handle_from_url

            h = (_handle_from_url(c["url"], "instagram") or "").lower()
            c = {**c, "handle": h}
        if not h or h in seen:
            continue
        seen.add(h)
        uniq.append(c)

    if not uniq:
        # Legacy single-path fallback
        single = find_profile_link(name, "instagram", company=company, known_url=instagram_url)
        if single.get("status") == "ok" and single.get("handle"):
            uniq = [{"handle": single["handle"], "url": single.get("url"), "method": single.get("method")}]
            discovery = single
        else:
            return {
                "status": "not_found",
                "discovery": discovery,
                "reason": discovery.get("reason") or "no Instagram profile candidates",
            }

    print(f"  [instagram] fetching profiles for {len(uniq[:MAX_FACE_CANDIDATES])} candidates…", flush=True)
    profiles = _fetch_profiles_parallel(sc_key, uniq[:MAX_FACE_CANDIDATES])

    face_result = None
    chosen = None
    if ref_photo and len(profiles) >= 1:
        try:
            from face_match import compare_faces

            face_inputs = []
            for p in profiles:
                pic = (p.get("profile") or {}).get("profile_pic_url")
                if not pic:
                    continue
                face_inputs.append(
                    {
                        "handle": p["handle"],
                        "full_name": (p.get("profile") or {}).get("full_name"),
                        "photo_url": pic,
                        "profile_url": p.get("profile_url"),
                    }
                )
            if face_inputs:
                face_result = compare_faces(ref_photo, face_inputs, person_name=name)
                accepted = (face_result or {}).get("accepted")
                if accepted:
                    handle = accepted["handle"]
                    chosen = next((p for p in profiles if p["handle"].lower() == handle.lower()), None)
                    print(f"  [instagram] face exact match @{handle} score={accepted.get('score')}", flush=True)
                elif (face_result or {}).get("best") and (face_result["best"].get("score") or 0) >= 55:
                    # Probable — still try verify on best, but mark face probable
                    handle = face_result["best"]["handle"]
                    chosen = next((p for p in profiles if p["handle"].lower() == handle.lower()), None)
                    print(
                        f"  [instagram] face probable @{handle} score={face_result['best'].get('score')}",
                        flush=True,
                    )
        except Exception as exc:
            face_result = {"status": "error", "error": str(exc)[:200]}
            print(f"  [instagram] face_match error: {exc}", flush=True)

    if chosen is None:
        chosen = profiles[0] if profiles else None

    if not chosen:
        return {
            "status": "not_found",
            "discovery": discovery,
            "face_match": face_result,
            "candidates_checked": [
                {"handle": p["handle"], "status": p.get("fetch_status")} for p in profiles
            ],
            "reason": "could not load Instagram profiles for candidates",
        }

    handle = chosen["handle"]
    profile_result = {
        "status": chosen.get("fetch_status") or "ok",
        "profile": chosen.get("profile"),
        "recent_posts": chosen.get("recent_posts") or [],
    }
    if chosen.get("fetch_status") == "error":
        err = chosen.get("error_payload") or {"status": "error", "error": "profile fetch failed"}
        return {
            **err,
            "handle": handle,
            "profile_url": chosen.get("profile_url"),
            "discovery": discovery,
            "face_match": face_result,
        }

    context = {
        "name": name,
        "company": company,
        "university": university,
        "place": place,
        **hints,
        "platform": "instagram",
    }
    # Don't pass huge photo URL blobs into verifier text
    context.pop("photo_url", None)
    context.pop("linkedin_photo_url", None)

    verification = verify_social_profile(
        context,
        {
            "handle": handle,
            "url": chosen.get("profile_url"),
            "profile": profile_result.get("profile"),
            "recent_posts": profile_result.get("recent_posts") or [],
            "fetch_status": profile_result.get("status"),
        },
    )

    confidence = (verification or {}).get("confidence") or "low"
    is_match = (verification or {}).get("match") is True and confidence in ("high", "medium")

    # Face exact upgrade
    if face_result and face_result.get("accepted"):
        is_match = True
        if confidence == "low":
            confidence = "high"
        elif confidence not in ("high", "medium"):
            confidence = "high"

    base = {
        "handle": handle,
        "profile_url": chosen.get("profile_url") or f"https://www.instagram.com/{handle}/",
        "discovery": discovery,
        "profile": profile_result.get("profile"),
        "recent_posts": profile_result.get("recent_posts") or [],
        "match_confidence": confidence,
        "match_score": (verification or {}).get("score"),
        "match_notes": (verification or {}).get("reasons") or [],
        "verification_summary": (verification or {}).get("summary"),
        "face_match": _public_face_match(face_result),
        "candidates_checked": [
            {
                "handle": p["handle"],
                "full_name": (p.get("profile") or {}).get("full_name"),
                "profile_pic_url": (p.get("profile") or {}).get("profile_pic_url"),
                "profile_url": p.get("profile_url"),
                "status": p.get("fetch_status"),
            }
            for p in profiles
        ],
    }

    if profile_result.get("status") == "no_public_data":
        return {**base, "status": "no_public_data", "reason": "Instagram account is private"}

    if not is_match and verification is not None and not (face_result and face_result.get("accepted")):
        # Surface probable face rankings for UI
        return {
            **base,
            "status": "ambiguous",
            "match_notes": ((verification or {}).get("reasons") or [])
            + ((verification or {}).get("red_flags") or []),
            "reason": "Instagram candidates found but none verified as the same person"
            + (
                " — see face_match.rankings for probable accounts"
                if face_result and face_result.get("rankings")
                else ""
            ),
        }

    if verification is None and not (face_result and face_result.get("accepted")):
        base["match_confidence"] = "medium"
        base["match_notes"] = ["verifier unavailable — kept top candidate"]

    posts = _fetch_posts_apidirect(handle, name)
    result = {**base, "status": "ok"}
    if posts is not None:
        result["apidirect_posts"] = posts
    return result


def _public_face_match(face_result: Optional[dict]) -> Optional[dict]:
    if not face_result or not isinstance(face_result, dict):
        return None
    return {
        "status": face_result.get("status"),
        "match_mode": face_result.get("match_mode"),
        "accepted": face_result.get("accepted"),
        "best": face_result.get("best"),
        "rankings": (face_result.get("rankings") or [])[:8],
        "error": face_result.get("error") or face_result.get("reason"),
    }


def _fetch_profiles_parallel(api_key: str, candidates: List[dict]) -> List[dict]:
    out: List[dict] = []

    def one(c: dict) -> dict:
        handle = (c.get("handle") or "").lstrip("@")
        url = c.get("url") or f"https://www.instagram.com/{handle}/"
        result = _fetch_profile(api_key, handle)
        return {
            "handle": handle,
            "profile_url": url,
            "method": c.get("method"),
            "fetch_status": result.get("status"),
            "profile": result.get("profile"),
            "recent_posts": result.get("recent_posts") or [],
            "error_payload": result if result.get("status") == "error" else None,
        }

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(candidates)))) as pool:
        futs = {pool.submit(one, c): c for c in candidates}
        for fut in as_completed(futs):
            try:
                out.append(fut.result())
            except Exception as exc:
                c = futs[fut]
                out.append(
                    {
                        "handle": c.get("handle"),
                        "profile_url": c.get("url"),
                        "fetch_status": "error",
                        "profile": None,
                        "recent_posts": [],
                        "error_payload": {"status": "error", "error": str(exc)[:200]},
                    }
                )
    # Preserve original candidate order
    order = {(c.get("handle") or "").lower(): i for i, c in enumerate(candidates)}
    out.sort(key=lambda p: order.get((p.get("handle") or "").lower(), 999))
    return out


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
                posts.append(
                    {
                        "id": item.get("id") or item.get("pk") or item.get("code"),
                        "caption": (caption or "")[:400] or None,
                        "like_count": item.get("like_count") or item.get("likes"),
                        "timestamp": item.get("taken_at") or item.get("timestamp") or item.get("date"),
                    }
                )
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
