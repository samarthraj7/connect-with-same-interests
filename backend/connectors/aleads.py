"""A-Leads contact enrichment (email / optional phone).

Post-identity enrichment — not a Find Me / photo replacement for Apollo.
Requires ALEADS_API_KEY (header x-api-key). Docs: https://docs.a-leads.co

Pay-only-for-valid model: find-email / find-phone charged on success.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import urlparse

import requests

ALEADS_BASE = "https://api.a-leads.co/gateway/v1/search"
TIMEOUT_DEFAULT = 30


def _api_key() -> Optional[str]:
    return (
        (os.environ.get("ALEADS_API_KEY") or os.environ.get("A_LEADS_API_KEY") or "").strip()
        or None
    )


def configured() -> bool:
    return bool(_api_key())


def _headers() -> dict[str, str]:
    key = _api_key() or ""
    return {
        "Content-Type": "application/json",
        "x-api-key": key,
        "Authorization": f"Bearer {key}",
    }


def _split_name(name: str) -> tuple[str, str]:
    parts = (name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _domain_from(company: Optional[str], domain: Optional[str], website: Optional[str] = None) -> Optional[str]:
    raw = (domain or website or "").strip()
    if not raw and company and "." in (company or "") and " " not in company.strip():
        raw = company.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urlparse(raw).hostname or ""
    except Exception:
        return None
    host = host.lower().removeprefix("www.")
    return host or None


def _linkedin_username(linkedin_url: Optional[str]) -> Optional[str]:
    if not linkedin_url:
        return None
    m = re.search(r"linkedin\.com/in/([^/?#]+)", linkedin_url, re.I)
    if not m:
        return None
    return m.group(1).strip().rstrip("/")


def enrich_contact(
    *,
    name: str,
    company: Optional[str] = None,
    domain: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    find_phone: Optional[bool] = None,
    timeout: float = TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    """After identity is locked: resolve work email (and optional phone) via A-Leads."""
    key = _api_key()
    if not key:
        return {"status": "skipped", "reason": "ALEADS_API_KEY not set"}

    name = (name or "").strip()
    if not name and not linkedin_url:
        return {"status": "error", "error": "name or linkedin_url required"}

    if find_phone is None:
        find_phone = (os.environ.get("ALEADS_FIND_PHONE") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    print(
        f"  [aleads] enrich {name!r} co={company!r} li={bool(linkedin_url)} phone={find_phone}",
        flush=True,
    )

    profile = search_person(
        name=name,
        company=company,
        linkedin_url=linkedin_url,
        timeout=timeout,
    )
    row = (profile.get("person") if profile.get("status") == "ok" else None) or {}

    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    if not first:
        first, last = _split_name(name)
    website = (
        row.get("website")
        or _domain_from(company, domain)
        or _domain_from(None, None, row.get("company_website"))
    )
    document_id = row.get("document_id")
    li_user = row.get("linkedin_username") or _linkedin_username(
        linkedin_url or row.get("linkedin_url")
    )

    out: dict[str, Any] = {
        "status": "ok",
        "source": "aleads",
        "full_name": row.get("full_name") or name,
        "title": row.get("title"),
        "linkedin_url": row.get("linkedin_url") or linkedin_url,
        "organization": {"name": row.get("company_name"), "domain": website},
        "document_id": document_id,
        "email": None,
        "email_quality": None,
        "phone": None,
        "profile": row or None,
        "search_status": profile.get("status"),
    }

    email_hit = find_email(
        first_name=first,
        last_name=last,
        website=website,
        document_id=document_id,
        timeout=timeout,
    )
    out["email_lookup"] = {k: email_hit.get(k) for k in ("status", "error", "quality", "result")}
    if email_hit.get("status") == "ok" and email_hit.get("email"):
        out["email"] = email_hit["email"]
        out["email_quality"] = email_hit.get("quality")

    if find_phone and li_user:
        phone_hit = find_phone_number(linkedin_username=li_user, timeout=timeout)
        out["phone_lookup"] = {k: phone_hit.get(k) for k in ("status", "error")}
        if phone_hit.get("status") == "ok" and phone_hit.get("phone"):
            out["phone"] = phone_hit["phone"]

    if not out.get("email") and not out.get("phone") and profile.get("status") != "ok":
        return {
            "status": profile.get("status") or "not_found",
            "source": "aleads",
            "error": profile.get("error") or "no A-Leads match",
            "email": None,
            "phone": None,
        }

    return out


def search_person(
    *,
    name: str,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    timeout: float = TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    """People advanced-search by LinkedIn username and/or full name."""
    if not _api_key():
        return {"status": "skipped", "reason": "ALEADS_API_KEY not set"}

    filters: dict[str, Any] = {}
    li_user = _linkedin_username(linkedin_url)
    if li_user:
        filters["member_linkedin_username"] = [li_user, linkedin_url]
    if name:
        filters["member_full_name"] = name.strip()
    if company:
        filters["organizations"] = [company.strip()]

    if not filters:
        return {"status": "error", "error": "no search filters"}

    try:
        resp = requests.post(
            f"{ALEADS_BASE}/advanced-search",
            headers=_headers(),
            json={"page": 1, "advanced_filters": filters},
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"status": "error", "error": "A-Leads unauthorized — check ALEADS_API_KEY"}
        if resp.status_code >= 400:
            return {
                "status": "error",
                "error": f"A-Leads search HTTP {resp.status_code}",
                "detail": (resp.text or "")[:400],
            }
        data = resp.json() if resp.content else {}
        rows = _extract_people_rows(data)
        if not rows:
            return {"status": "not_found"}

        best = _pick_best_row(rows, name=name, linkedin_username=li_user, company=company)
        return {"status": "ok", "person": best, "matches": len(rows)}
    except requests.RequestException as e:
        return {"status": "error", "error": str(e)}


def find_email(
    *,
    first_name: str,
    last_name: str,
    website: Optional[str] = None,
    document_id: Optional[str] = None,
    timeout: float = TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    if not _api_key():
        return {"status": "skipped", "reason": "ALEADS_API_KEY not set"}
    if not first_name or (not website and not document_id):
        return {"status": "skipped", "reason": "need first_name + website or document_id"}

    payload: dict[str, Any] = {
        "data": {
            "first_name": first_name,
            "last_name": last_name or first_name,
        }
    }
    if website:
        payload["data"]["website"] = website
    if document_id:
        payload["data"]["document_id"] = document_id

    try:
        resp = requests.post(
            f"{ALEADS_BASE}/find-email",
            headers=_headers(),
            json=payload,
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"status": "error", "error": "A-Leads unauthorized — check ALEADS_API_KEY"}
        if resp.status_code >= 400:
            return {
                "status": "error",
                "error": f"A-Leads find-email HTTP {resp.status_code}",
                "detail": (resp.text or "")[:400],
            }
        data = resp.json() if resp.content else {}
        body = ((data.get("data") or {}).get("response")) or data.get("response") or {}
        email = body.get("email")
        if not email:
            return {"status": "not_found", "raw": body}
        return {
            "status": "ok",
            "email": email,
            "quality": body.get("quality"),
            "result": body.get("result"),
            "catch_all_status": body.get("catch_all_status"),
        }
    except requests.RequestException as e:
        return {"status": "error", "error": str(e)}


def find_phone_number(
    *,
    linkedin_username: str,
    timeout: float = TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    """Costs ~15 credits on success — gated by ALEADS_FIND_PHONE."""
    if not _api_key():
        return {"status": "skipped", "reason": "ALEADS_API_KEY not set"}
    if not linkedin_username:
        return {"status": "skipped", "reason": "linkedin_username required"}

    try:
        resp = requests.post(
            f"{ALEADS_BASE}/find-phone",
            headers=_headers(),
            json={"data": {"linkedin_username": linkedin_username}},
            timeout=timeout,
        )
        if resp.status_code == 401:
            return {"status": "error", "error": "A-Leads unauthorized — check ALEADS_API_KEY"}
        if resp.status_code >= 400:
            return {
                "status": "error",
                "error": f"A-Leads find-phone HTTP {resp.status_code}",
                "detail": (resp.text or "")[:400],
            }
        data = resp.json() if resp.content else {}
        body = ((data.get("data") or {}).get("response")) or data.get("response") or {}
        phone = body.get("phone_number") or body.get("phone")
        if not phone:
            return {"status": "not_found"}
        return {"status": "ok", "phone": phone}
    except requests.RequestException as e:
        return {"status": "error", "error": str(e)}


def _extract_people_rows(data: dict) -> list[dict]:
    for key in ("data", "results", "leads", "people", "records"):
        block = data.get(key)
        if isinstance(block, list) and block:
            return [r for r in block if isinstance(r, dict)]
        if isinstance(block, dict):
            for inner in ("results", "leads", "people", "records", "data"):
                rows = block.get(inner)
                if isinstance(rows, list) and rows:
                    return [r for r in rows if isinstance(r, dict)]
    if isinstance(data.get("response"), list):
        return [r for r in data["response"] if isinstance(r, dict)]
    return []


def _pick_best_row(
    rows: list[dict],
    *,
    name: str,
    linkedin_username: Optional[str],
    company: Optional[str],
) -> dict[str, Any]:
    from name_match import is_exact_name_match, normalize_person_name

    target = normalize_person_name(name)
    co = (company or "").strip().lower()
    best = rows[0]
    best_score = -1
    for r in rows:
        full = (
            r.get("member_full_name")
            or f"{r.get('member_name_first') or ''} {r.get('member_name_last') or ''}".strip()
        )
        score = 0
        li = (r.get("member_linkedin_username") or "").lower()
        if linkedin_username and li == linkedin_username.lower():
            score += 100
        if full and is_exact_name_match(name, full):
            score += 50
        elif full and normalize_person_name(full) == target:
            score += 40
        if co and co in (r.get("company_name") or "").lower():
            score += 10
        if score > best_score:
            best_score = score
            best = r

    website = best.get("website") or best.get("company_website") or best.get("company_domain")
    li_url = best.get("member_linkedin_url")
    li_user = best.get("member_linkedin_username")
    if not li_url and li_user:
        li_url = f"https://www.linkedin.com/in/{li_user}"

    return {
        "full_name": best.get("member_full_name")
        or f"{best.get('member_name_first') or ''} {best.get('member_name_last') or ''}".strip(),
        "first_name": best.get("member_name_first"),
        "last_name": best.get("member_name_last"),
        "title": best.get("job_title"),
        "company_name": best.get("company_name"),
        "website": _domain_from(None, None, website) if website else None,
        "linkedin_url": li_url,
        "linkedin_username": li_user,
        "document_id": best.get("document_id"),
        "location": best.get("member_location_raw_address")
        or best.get("member_location_country"),
        "raw": best,
    }
