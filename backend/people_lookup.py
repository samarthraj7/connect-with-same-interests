"""Locate a previously researched public person without re-running connectors.

Match priority:
1) LinkedIn URL exact (normalized)
2) Name + company
3) Name + university
4) Name alone only if exactly one dossier exists for that name
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from storage import PROFILES_DIR, ProfileStore, _slugify


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _norm_li(url: Optional[str]) -> str:
    if not url:
        return ""
    u = url.strip().lower().rstrip("/")
    u = u.replace("http://", "https://").replace("www.", "")
    if "linkedin.com/in/" in u:
        return u.split("linkedin.com/in/")[-1].split("?")[0].strip("/")
    return u


def find_cached_person(
    store: ProfileStore,
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return a profile record that already has a usable briefing, or None."""
    name_n = _norm_name(name)
    if not name_n:
        return None

    li = _norm_li(linkedin_url)
    company_n = _norm_name(company or "")
    uni_n = _norm_name(university or "")

    # Fast path: slug file
    direct = store.load(name, company)
    if direct and _usable(direct) and _identity_ok(direct, name_n, company_n, uni_n, li):
        return direct

    matches: list[dict] = []
    for path in Path(store.base_dir).glob("*.json"):
        try:
            rec = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not _usable(rec):
            continue
        if _norm_name(rec.get("name") or "") != name_n:
            continue
        if not _identity_ok(rec, name_n, company_n, uni_n, li):
            continue
        matches.append(rec)

    if not matches:
        return None
    if li:
        for m in matches:
            if _norm_li((m.get("contact") or {}).get("linkedin_url") or m.get("linkedin_url")) == li:
                return m
    if company_n:
        for m in matches:
            if _norm_name(m.get("company") or "") == company_n:
                return m
    if uni_n:
        for m in matches:
            if uni_n in _norm_name(m.get("university") or "") or uni_n in _norm_name(
                str((m.get("latest_summary") or {}).get("education") or "")
            ):
                return m
    if len(matches) == 1 and (company_n or uni_n or li):
        return matches[0]
    # Name-only: only reuse if unique
    if not company_n and not uni_n and not li and len(matches) == 1:
        return matches[0]
    return None


def _usable(rec: dict) -> bool:
    summary = rec.get("latest_summary") or {}
    sources = rec.get("latest_sources") or {}
    return bool(summary.get("status") == "ok" or summary.get("summary")) and bool(sources or summary)


def _identity_ok(
    rec: dict,
    name_n: str,
    company_n: str,
    uni_n: str,
    li: str,
) -> bool:
    if _norm_name(rec.get("name") or "") != name_n:
        return False
    rec_li = _norm_li((rec.get("contact") or {}).get("linkedin_url") or rec.get("linkedin_url"))
    if li and rec_li and li != rec_li:
        return False
    if li and not rec_li and (company_n or uni_n):
        # LinkedIn provided but cache has none — still allow company/uni match
        pass
    if company_n:
        rec_co = _norm_name(rec.get("company") or "")
        if rec_co and company_n not in rec_co and rec_co not in company_n:
            return False
    return True


def public_dossier_from_record(rec: dict) -> dict[str, Any]:
    """Fields safe to show anyone who searches this person."""
    summary = rec.get("latest_summary") or {}
    sources = rec.get("latest_sources") or {}
    return {
        "visibility": "public",
        "name": rec.get("name"),
        "company": rec.get("company"),
        "university": rec.get("university"),
        "linkedin_url": (rec.get("contact") or {}).get("linkedin_url") or rec.get("linkedin_url"),
        "contact": {
            k: v
            for k, v in (rec.get("contact") or {}).items()
            if k
            in (
                "linkedin_url",
                "github_username",
                "website",
                "twitter_handle",
                "title",
            )
            and v
        },
        "summary": summary,
        "sources": sources,
        "portfolios": ((sources.get("public_web") or {}).get("portfolios") or []),
        "updated_at": rec.get("updated_at"),
        "slug": _slugify(rec.get("name") or "", rec.get("company")),
        "claimed_user_id": rec.get("claimed_user_id"),
    }
