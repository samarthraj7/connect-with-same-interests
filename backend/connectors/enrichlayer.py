"""Enrich Layer — LinkedIn profile / photo enrichment.

Requires ENRICHLAYER_API_KEY (Bearer). Docs: https://enrichlayer.com/docs
Used when Apollo has no photo but we have (or can resolve) a LinkedIn URL.
"""

from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import quote

import requests

ENRICHLAYER_BASE = "https://enrichlayer.com/api/v2"


def _api_key() -> Optional[str]:
    return (
        (os.environ.get("ENRICHLAYER_API_KEY") or "").strip()
        or (os.environ.get("ENRICH_LAYER_API_KEY") or "").strip()
        or None
    )


def configured() -> bool:
    return bool(_api_key())


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _normalize_linkedin_url(url: str) -> Optional[str]:
    u = (url or "").strip()
    if not u:
        return None
    if not u.startswith("http"):
        u = "https://" + u.lstrip("/")
    # Drop query/hash; Enrich Layer rejects some hashed-ID forms
    u = u.split("?")[0].split("#")[0].rstrip("/")
    if "linkedin.com/" not in u.lower():
        return None
    return u + "/"


def profile_picture(linkedin_url: str, *, timeout: float = 12) -> dict[str, Any]:
    """GET /person/profile-picture — 0 credits when cached photo exists."""
    key = _api_key()
    if not key:
        return {"status": "skipped", "reason": "ENRICHLAYER_API_KEY not set"}
    profile_url = _normalize_linkedin_url(linkedin_url)
    if not profile_url:
        return {"status": "skipped", "reason": "invalid linkedin url"}

    print(f"  [enrichlayer] profile-picture {profile_url}", flush=True)
    try:
        resp = requests.get(
            f"{ENRICHLAYER_BASE}/person/profile-picture",
            headers=_headers(key),
            params={"person_profile_url": profile_url},
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"status": "error", "error": "Enrich Layer unauthorized — check ENRICHLAYER_API_KEY"}
        if resp.status_code == 404:
            return {"status": "not_found"}
        if resp.status_code >= 400:
            return {
                "status": "error",
                "error": f"Enrich Layer HTTP {resp.status_code}",
                "detail": (resp.text or "")[:400],
            }
        data = resp.json() if resp.content else {}
        pic = (
            data.get("tmp_profile_pic_url")
            or data.get("profile_pic_url")
            or data.get("profile_picture_url")
        )
        if not pic:
            return {"status": "not_found", "raw": data}
        return {
            "status": "ok",
            "photo_url": pic,
            "linkedin_url": profile_url,
            "source": "enrichlayer",
        }
    except requests.RequestException as e:
        return {"status": "error", "error": str(e)}


def fetch_profile(linkedin_url: str, *, timeout: float = 20) -> dict[str, Any]:
    """GET /profile with use_cache=if-present — cheaper than forcing live fetch."""
    key = _api_key()
    if not key:
        return {"status": "skipped", "reason": "ENRICHLAYER_API_KEY not set"}
    profile_url = _normalize_linkedin_url(linkedin_url)
    if not profile_url:
        return {"status": "skipped", "reason": "invalid linkedin url"}

    print(f"  [enrichlayer] profile {profile_url}", flush=True)
    try:
        resp = requests.get(
            f"{ENRICHLAYER_BASE}/profile",
            headers=_headers(key),
            params={
                "profile_url": profile_url,
                "use_cache": "if-present",
                "fallback_to_cache": "on-error",
            },
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"status": "error", "error": "Enrich Layer unauthorized — check ENRICHLAYER_API_KEY"}
        if resp.status_code == 404:
            return {"status": "not_found"}
        if resp.status_code >= 400:
            return {
                "status": "error",
                "error": f"Enrich Layer HTTP {resp.status_code}",
                "detail": (resp.text or "")[:400],
            }
        data = resp.json() if resp.content else {}
        return _normalize_profile(data, linkedin_url=profile_url)
    except requests.RequestException as e:
        return {"status": "error", "error": str(e)}


def resolve_person(
    *,
    name: str,
    company: Optional[str] = None,
    location: Optional[str] = None,
    title: Optional[str] = None,
    timeout: float = 20,
) -> dict[str, Any]:
    """GET /profile/resolve — name + company → LinkedIn URL (+ optional cached profile).

    Requires company (name or domain). Uses similarity_checks=skip so nulls
    don't burn credits.
    """
    key = _api_key()
    if not key:
        return {"status": "skipped", "reason": "ENRICHLAYER_API_KEY not set"}
    name = (name or "").strip()
    company = (company or "").strip()
    if not name or not company:
        return {"status": "skipped", "reason": "name and company required for resolve"}

    parts = name.split(None, 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""

    params: dict[str, str] = {
        "first_name": first,
        "company_domain": company,
        "similarity_checks": "skip",
        "enrich_profile": "enrich",
    }
    if last:
        params["last_name"] = last
    if location:
        params["location"] = location.strip()
    if title:
        params["title"] = title.strip()

    print(f"  [enrichlayer] resolve {first} {last} @ {company}", flush=True)
    try:
        resp = requests.get(
            f"{ENRICHLAYER_BASE}/profile/resolve",
            headers=_headers(key),
            params=params,
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"status": "error", "error": "Enrich Layer unauthorized — check ENRICHLAYER_API_KEY"}
        if resp.status_code == 404:
            return {"status": "not_found"}
        if resp.status_code >= 400:
            return {
                "status": "error",
                "error": f"Enrich Layer HTTP {resp.status_code}",
                "detail": (resp.text or "")[:400],
            }
        data = resp.json() if resp.content else {}
        url = data.get("url") or data.get("linkedin_profile_url")
        profile = data.get("profile") if isinstance(data.get("profile"), dict) else None
        if profile:
            out = _normalize_profile(profile, linkedin_url=url)
            if url and not out.get("linkedin_url"):
                out["linkedin_url"] = url
            return out
        if url:
            pic = profile_picture(url, timeout=timeout)
            if pic.get("status") == "ok":
                return {
                    "status": "ok",
                    "linkedin_url": url,
                    "photo_url": pic.get("photo_url"),
                    "source": "enrichlayer",
                }
            return {"status": "ok", "linkedin_url": url, "source": "enrichlayer"}
        return {"status": "not_found"}
    except requests.RequestException as e:
        return {"status": "error", "error": str(e)}


def enrich_photo_for_candidate(
    *,
    name: Optional[str] = None,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    role: Optional[str] = None,
    location: Optional[str] = None,
    timeout: float = 12,
) -> dict[str, Any]:
    """Best-effort LinkedIn photo for a Find Me candidate.

    Prefer free profile-picture when URL known; else resolve with company.
    """
    if not configured():
        return {"status": "skipped", "reason": "ENRICHLAYER_API_KEY not set"}

    li = _normalize_linkedin_url(linkedin_url or "")
    if li:
        pic = profile_picture(li, timeout=timeout)
        if pic.get("status") == "ok" and pic.get("photo_url"):
            return pic
        # Fall through to full profile (costs ~1 credit) if picture miss
        prof = fetch_profile(li, timeout=max(timeout, 18))
        if prof.get("status") == "ok" and prof.get("photo_url"):
            return prof
        if pic.get("status") != "skipped":
            return pic if pic.get("status") != "ok" else {"status": "not_found"}
        return prof

    if name and company:
        return resolve_person(
            name=name,
            company=company,
            location=location,
            title=role,
            timeout=max(timeout, 18),
        )
    return {"status": "skipped", "reason": "need linkedin_url or name+company"}


def _normalize_profile(data: dict, *, linkedin_url: Optional[str] = None) -> dict[str, Any]:
    if not data:
        return {"status": "not_found"}
    pic = data.get("profile_pic_url") or data.get("tmp_profile_pic_url")
    identifier = data.get("public_identifier")
    li = linkedin_url
    if not li and identifier:
        li = f"https://www.linkedin.com/in/{quote(str(identifier).strip('/'))}/"
    experiences = data.get("experiences") or []
    company = None
    title = None
    if experiences:
        company = experiences[0].get("company")
        title = experiences[0].get("title")
    return {
        "status": "ok",
        "source": "enrichlayer",
        "full_name": data.get("full_name")
        or f"{data.get('first_name') or ''} {data.get('last_name') or ''}".strip(),
        "headline": data.get("headline") or data.get("occupation"),
        "title": title or data.get("occupation"),
        "photo_url": pic,
        "linkedin_url": li,
        "city": data.get("city"),
        "state": data.get("state"),
        "country": data.get("country_full_name") or data.get("country"),
        "organization": {"name": company} if company else {},
        "summary": data.get("summary"),
    }
