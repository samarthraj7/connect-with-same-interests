"""Apollo.io people enrichment connector.

Requires APOLLO_API_KEY. Match/enrich by name + company/domain/LinkedIn URL.
Returns licensed email/title/org/socials when available — never invents.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests

APOLLO_BASE = "https://api.apollo.io/api/v1"


def _api_key() -> Optional[str]:
    return (os.environ.get("APOLLO_API_KEY") or "").strip() or None


def enrich_person(
    *,
    name: str,
    company: Optional[str] = None,
    domain: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    email: Optional[str] = None,
) -> dict[str, Any]:
    """People match/enrich via Apollo. Primary enrichment path when key is set."""
    key = _api_key()
    if not key:
        return {"status": "skipped", "reason": "APOLLO_API_KEY not set"}

    name = (name or "").strip()
    if not name and not email and not linkedin_url:
        return {"status": "error", "error": "name, email, or linkedin_url required"}

    parts = name.split(None, 1) if name else ["", ""]
    first = parts[0] if parts else ""
    last = parts[1] if len(parts) > 1 else ""

    payload: dict[str, Any] = {
        "api_key": key,
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
    }
    if first:
        payload["first_name"] = first
    if last:
        payload["last_name"] = last
    if name and not last:
        payload["name"] = name
    if company:
        payload["organization_name"] = company.strip()
    if domain:
        payload["domain"] = domain.strip()
    if linkedin_url:
        payload["linkedin_url"] = linkedin_url.strip()
    if email:
        payload["email"] = email.strip()

    print(f"  [apollo] enrich {name or email} @ {company or domain or linkedin_url or '?'}")
    try:
        # Prefer people/match; fall back to enrichment endpoint shape.
        url = f"{APOLLO_BASE}/people/match"
        resp = requests.post(url, json=payload, timeout=45)
        if resp.status_code == 401:
            return {"status": "error", "error": "Apollo unauthorized — check APOLLO_API_KEY"}
        if resp.status_code == 422 or resp.status_code == 404:
            # Try search as weaker match
            return _search_fallback(key, name=name, company=company)
        if resp.status_code >= 400:
            return {
                "status": "error",
                "error": f"Apollo HTTP {resp.status_code}",
                "detail": (resp.text or "")[:400],
            }
        data = resp.json() if resp.content else {}
        person = data.get("person") or data.get("people", [None])[0]
        if not person:
            return _search_fallback(key, name=name, company=company)
        return _normalize_person(person)
    except requests.RequestException as e:
        return {"status": "error", "error": str(e)}


def _search_fallback(key: str, *, name: str, company: Optional[str]) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = {
            "api_key": key,
            "q_keywords": name,
            "page": 1,
            "per_page": 3,
        }
        if company:
            payload["q_organization_name"] = company
        resp = requests.post(f"{APOLLO_BASE}/mixed_people/search", json=payload, timeout=45)
        if resp.status_code >= 400:
            return {"status": "not_found", "error": f"Apollo search HTTP {resp.status_code}"}
        data = resp.json() if resp.content else {}
        people = data.get("people") or []
        if not people:
            return {"status": "not_found"}
        # Prefer exact-ish name match
        target = name.lower()
        best = people[0]
        for p in people:
            pn = (p.get("name") or f"{p.get('first_name','')} {p.get('last_name','')}").strip().lower()
            if pn == target:
                best = p
                break
        out = _normalize_person(best)
        out["match_mode"] = "search"
        return out
    except requests.RequestException as e:
        return {"status": "error", "error": str(e)}


def _normalize_person(person: dict) -> dict[str, Any]:
    org = person.get("organization") or {}
    employment = person.get("employment_history") or []
    titles = []
    for job in employment[:6]:
        line = " · ".join(
            x
            for x in [
                job.get("title"),
                job.get("organization_name") or (job.get("organization") or {}).get("name"),
                job.get("start_date"),
            ]
            if x
        )
        if line:
            titles.append(line)

    email = person.get("email")
    if email and str(email).endswith("email_not_unlocked@"):
        email = None

    return {
        "status": "ok",
        "source": "apollo",
        "full_name": person.get("name")
        or f"{person.get('first_name') or ''} {person.get('last_name') or ''}".strip(),
        "title": person.get("title"),
        "headline": person.get("headline"),
        "email": email,
        "linkedin_url": person.get("linkedin_url"),
        "twitter_url": person.get("twitter_url"),
        "github_url": person.get("github_url"),
        "photo_url": person.get("photo_url"),
        "city": person.get("city"),
        "state": person.get("state"),
        "country": person.get("country"),
        "organization": {
            "name": org.get("name") or person.get("organization_name"),
            "domain": org.get("primary_domain") or org.get("website_url"),
            "industry": org.get("industry"),
        },
        "employment_history": titles,
        "seniority": person.get("seniority"),
        "departments": person.get("departments") or [],
        "raw_id": person.get("id"),
    }
