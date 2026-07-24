"""Instagram deep fetch: Google/name discover → name filter → ScrapeCreators → face match.

Order matters: ScrapeCreators is only called for handles that already passed
name verification (never SC name-search for discovery).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import requests

from connectors.social_find import find_profile_candidates, find_profile_link, rank_profile_candidates
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
    search_constraints: Optional[dict] = None,
) -> dict:
    hints = identity_hints or {}
    ref_photo = (hints.get("photo_url") or hints.get("linkedin_photo_url") or "").strip()

    # Channel discovery + Google/name find — ALWAYS merge (OpenCLI alone misses creative handles)
    hint = None
    if search_constraints and isinstance(search_constraints, dict):
        hint = (search_constraints.get("distinguishable_factor") or "").strip() or None
    if not hint:
        hint = (hints.get("distinguishable_factor") or hints.get("hint") or "").strip() or None

    channel_cands = _channel_instagram_candidates(
        name, company=company, university=university, hint=hint, known_url=instagram_url
    )
    discovery = find_profile_candidates(
        name,
        "instagram",
        company=company,
        university=university,
        distinguishable_factor=hint,
        known_url=instagram_url,
        max_candidates=12,
        search_constraints=search_constraints,
    )
    # Prefer channel hits first, then Google/username guesses
    cands = list(channel_cands or []) + list(discovery.get("candidates") or [])
    if channel_cands:
        discovery = {
            **(discovery if isinstance(discovery, dict) else {}),
            "method": f"channel+{(discovery or {}).get('method') or 'google'}",
            "channel_count": len(channel_cands),
        }
    sc_key = os.environ.get("SCRAPECREATORS_API_KEY")

    # Merge any orchestrator-provided URLs
    extra = []
    for u in candidate_urls or []:
        if u:
            extra.append({"url": u, "handle": None, "method": "seed_url", "title": name})
    cands = list(cands) + extra
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
            uniq = [{"handle": single["handle"], "url": single.get("url"), "method": single.get("method"), "title": name}]
            discovery = single
        else:
            return {
                "status": "not_found",
                "discovery": discovery,
                "reason": discovery.get("reason") or "no Instagram profile candidates",
            }

    # Prefer channel deep-fetch (OpenCLI/browser) before ScrapeCreators
    profiles = _fetch_profiles_via_channels(uniq[:MAX_FACE_CANDIDATES])
    if not profiles and sc_key:
        print(
            f"  [instagram] name-verified candidates → ScrapeCreators deep-fetch "
            f"({len(uniq[:MAX_FACE_CANDIDATES])}): {[c.get('handle') for c in uniq[:MAX_FACE_CANDIDATES]]}",
            flush=True,
        )
        profiles = _fetch_profiles_parallel(sc_key, uniq[:MAX_FACE_CANDIDATES])
    elif not profiles and not sc_key:
        return {
            "status": "skipped",
            "reason": "no OpenCLI/browser session and SCRAPECREATORS_API_KEY not set",
            "discovery": discovery,
        }

    # Drop profiles whose SC full_name clearly mismatches the query name
    profiles = [p for p in profiles if _sc_name_plausible(name, p)] or profiles

    face_result = None
    face_by_handle: dict = {}
    # Face recognition disabled for now — profile photos are often outdated/mismatched,
    # so we rely on name + bio/company/uni/hint ranking. Keep the block for easy re-enable.
    # if ref_photo and len(profiles) >= 1:
    #     try:
    #         from face_match import compare_faces
    #
    #         face_inputs = []
    #         for p in profiles:
    #             pic = (p.get("profile") or {}).get("profile_pic_url")
    #             if not pic:
    #                 continue
    #             face_inputs.append(
    #                 {
    #                     "handle": p["handle"],
    #                     "full_name": (p.get("profile") or {}).get("full_name"),
    #                     "photo_url": pic,
    #                     "profile_url": p.get("profile_url"),
    #                 }
    #             )
    #         if face_inputs:
    #             face_result = compare_faces(ref_photo, face_inputs, person_name=name)
    #             for row in (face_result or {}).get("rankings") or []:
    #                 if isinstance(row, dict) and row.get("handle"):
    #                     face_by_handle[str(row["handle"]).lower()] = row
    #             accepted = (face_result or {}).get("accepted")
    #             if accepted:
    #                 print(
    #                     f"  [instagram] face exact match @{accepted.get('handle')} "
    #                     f"score={accepted.get('score')}",
    #                     flush=True,
    #                 )
    #     except Exception as exc:
    #         face_result = {"status": "error", "error": str(exc)[:200]}
    #         print(f"  [instagram] face_match error: {exc}", flush=True)
    print("  [instagram] face recognition skipped (disabled — using bio/name ranking)", flush=True)

    # Multi-signal rank: bio/org/hint + name (+ face if re-enabled) — same display name, different @handles
    linkedin_slug = (hints.get("linkedin_slug") or "").strip() or None
    ranked = _rank_instagram_varieties(
        profiles,
        name=name,
        company=company,
        university=university,
        place=place,
        hint=hint,
        linkedin_slug=linkedin_slug,
        face_by_handle=face_by_handle,
    )
    for row in ranked[:6]:
        print(
            f"  [instagram] variety @{row['handle']} total={row['identity_score']:.1f} "
            f"face={row.get('face_score')} signals={row.get('signals')}",
            flush=True,
        )

    chosen = None
    # Face-accept path (re-enable with face block above):
    # accepted = (face_result or {}).get("accepted") if face_result else None
    # if accepted: ...

    if chosen is None and ranked:
        best, second = ranked[0], ranked[1] if len(ranked) > 1 else None
        gap = best["identity_score"] - (second["identity_score"] if second else 0)
        # Clear winner on bio/name/org signals (face not required)
        if best["identity_score"] >= 40 and (gap >= 12 or len(ranked) == 1):
            chosen = next((p for p in profiles if p["handle"].lower() == best["handle"].lower()), None)
            print(
                f"  [instagram] multi-signal pick @{best['handle']} "
                f"(score={best['identity_score']:.1f} gap={gap:.1f})",
                flush=True,
            )
        elif best["identity_score"] >= 28 and gap >= 8:
            chosen = next((p for p in profiles if p["handle"].lower() == best["handle"].lower()), None)
            print(
                f"  [instagram] soft pick @{best['handle']} "
                f"(score={best['identity_score']:.1f} gap={gap:.1f})",
                flush=True,
            )

    if not chosen:
        reason = (
            "multiple Instagram handles for this name — no clear bio/name winner"
            if ranked and len(ranked) > 1
            else "could not load Instagram profiles for candidates"
        )
        return {
            "status": "ambiguous" if ranked else "not_found",
            "discovery": discovery,
            "face_match": face_result,
            "handle_varieties": [
                {
                    "handle": r["handle"],
                    "full_name": r.get("full_name"),
                    "profile_url": r.get("profile_url"),
                    "profile_pic_url": r.get("profile_pic_url"),
                    "identity_score": r["identity_score"],
                    "face_score": r.get("face_score"),
                    "signals": r.get("signals"),
                }
                for r in ranked[:8]
            ],
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
            "reason": reason,
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

    # Face exact upgrade — disabled while face recognition is off
    # if face_result and face_result.get("accepted"):
    #     if (verification or {}).get("match") is False and confidence == "low":
    #         print("  [instagram] face accepted but text verify rejected — keeping ambiguous", flush=True)
    #     else:
    #         is_match = True
    #         confidence = "high"
    # Without face: accept medium+ text verify, or soft-pick with exact name + bio signals
    if not is_match and ranked:
        best = next((r for r in ranked if r["handle"].lower() == handle.lower()), None)
        if best and best["identity_score"] >= 40 and (
            "exact_name" in (best.get("signals") or []) or "bio:" in str(best.get("signals") or [])
        ):
            is_match = True
            confidence = "medium"
            print(f"  [instagram] accepting @{handle} via bio/name signals (no face)", flush=True)

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
        "handle_varieties": [
            {
                "handle": r["handle"],
                "full_name": r.get("full_name"),
                "profile_url": r.get("profile_url"),
                "identity_score": r["identity_score"],
                "face_score": r.get("face_score"),
                "signals": r.get("signals"),
                "picked": r["handle"].lower() == handle.lower(),
            }
            for r in ranked[:8]
        ] if ranked else None,
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


def _channel_instagram_candidates(
    name: str,
    *,
    company: Optional[str] = None,
    university: Optional[str] = None,
    hint: Optional[str] = None,
    known_url: Optional[str] = None,
) -> List[dict]:
    """OpenCLI then browser session discovery — try several queries (name ≠ LinkedIn slug)."""
    queries: List[str] = []
    if name:
        queries.append(name.strip())
    if name and company:
        queries.append(f"{name} {company}".strip())
    if name and university:
        queries.append(f"{name} {university}".strip())
    if name and hint:
        queries.append(f"{name} {hint.split(',')[0].strip()}".strip())
    # De-dupe
    seen_q = set()
    ordered = []
    for q in queries:
        k = q.lower()
        if k in seen_q or len(q) < 3:
            continue
        seen_q.add(k)
        ordered.append(q)

    out: List[dict] = []
    seen_h: set = set()
    if known_url:
        from connectors.social_find import _handle_from_url

        h = _handle_from_url(known_url, "instagram")
        if h:
            seen_h.add(h.lower())
            out.append({"handle": h, "url": known_url, "method": "known_url", "title": name})

    def _extend(rows: list, method: str) -> None:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            h = (row.get("handle") or "").lstrip("@").lower()
            if not h or h in seen_h:
                continue
            seen_h.add(h)
            out.append({**row, "method": row.get("method") or method})

    try:
        from connectors import opencli_social

        if opencli_social.configured():
            for q in ordered[:4]:
                res = opencli_social.search_instagram(q, limit=8)
                if res.get("status") == "ok":
                    _extend(res.get("candidates") or [], "opencli")
                    print(
                        f"  [instagram] opencli query={q!r} n={len(res.get('candidates') or [])}",
                        flush=True,
                    )
    except Exception as exc:
        print(f"  [instagram] opencli discovery skip: {exc}", flush=True)

    if len(out) < 3:
        try:
            from connectors import browser_social

            if browser_social.configured("instagram"):
                for q in ordered[:3]:
                    res = browser_social.search_instagram(q, limit=8)
                    if res.get("status") == "ok":
                        _extend(res.get("candidates") or [], "browser")
                        print(
                            f"  [instagram] browser query={q!r} n={len(res.get('candidates') or [])}",
                            flush=True,
                        )
        except Exception as exc:
            print(f"  [instagram] browser discovery skip: {exc}", flush=True)
    return out


def _rank_instagram_varieties(
    profiles: List[dict],
    *,
    name: str,
    company: Optional[str],
    university: Optional[str],
    place: Optional[str],
    hint: Optional[str],
    linkedin_slug: Optional[str],
    face_by_handle: dict,
) -> List[dict]:
    """Score same-name / different-@handle candidates so the right variety can win."""
    return rank_profile_candidates(
        profiles,
        name=name,
        company=company,
        university=university,
        place=place,
        hint=hint,
        linkedin_slug=linkedin_slug,
        face_by_handle=face_by_handle,
        profile_url_template="https://www.instagram.com/{handle}/",
    )


def _fetch_profiles_via_channels(candidates: List[dict]) -> List[dict]:
    """Deep-fetch profiles via OpenCLI / browser before ScrapeCreators."""
    out: List[dict] = []
    for c in candidates:
        handle = (c.get("handle") or "").lstrip("@")
        if not handle:
            continue
        fetched = None
        try:
            from connectors import opencli_social

            if opencli_social.configured():
                fetched = opencli_social.fetch_instagram_user(handle)
        except Exception:
            fetched = None
        if not fetched or fetched.get("status") != "ok":
            try:
                from connectors import browser_social

                if browser_social.configured("instagram"):
                    fetched = browser_social.fetch_instagram_user(handle)
            except Exception:
                fetched = None
        if fetched and fetched.get("status") == "ok":
            out.append(
                {
                    "handle": handle,
                    "profile_url": fetched.get("profile_url") or f"https://www.instagram.com/{handle}/",
                    "profile": fetched.get("profile"),
                    "recent_posts": fetched.get("recent_posts") or [],
                    "fetch_status": "ok",
                    "provider": fetched.get("provider"),
                }
            )
    if out:
        print(f"  [instagram] channel deep-fetch ok n={len(out)}", flush=True)
    return out


def _sc_name_plausible(name: str, profile_row: dict) -> bool:
    """After SC profile fetch, drop handles whose full_name clearly isn't the person."""
    import re

    full = ((profile_row.get("profile") or {}).get("full_name") or "").strip()
    if not full:
        return True  # no name on profile — keep for face match
    tokens = [t.lower() for t in re.split(r"[^A-Za-z]+", name or "") if len(t) > 1]
    if not tokens:
        return True
    fl = full.lower()
    if tokens[0] in fl and (len(tokens) < 2 or tokens[-1] in fl):
        return True
    # At least first name must appear
    return tokens[0] in fl


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
