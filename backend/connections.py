"""Parse LinkedIn connections CSV export and fuzzy-match against researched people."""

from __future__ import annotations

import csv
import io
import re
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional


def parse_linkedin_connections_csv(text: str) -> list[dict[str, Any]]:
    """Parse LinkedIn Connections.csv (may have Notes header rows)."""
    raw = (text or "").lstrip("\ufeff")
    # LinkedIn exports often start with Notes / "Connections" preamble before the header.
    lines = raw.splitlines()
    header_idx = 0
    for i, line in enumerate(lines[:40]):
        lower = line.lower()
        if "first name" in lower and "last name" in lower:
            header_idx = i
            break
    body = "\n".join(lines[header_idx:])
    reader = csv.DictReader(io.StringIO(body))
    out: list[dict[str, Any]] = []
    for row in reader:
        if not row:
            continue
        first = (row.get("First Name") or row.get("first_name") or row.get("FirstName") or "").strip()
        last = (row.get("Last Name") or row.get("last_name") or row.get("LastName") or "").strip()
        name = f"{first} {last}".strip() or (row.get("Name") or row.get("Full Name") or "").strip()
        if not name:
            continue
        company = (
            row.get("Company") or row.get("company") or row.get("Organization") or ""
        ).strip() or None
        linkedin_url = (
            row.get("URL") or row.get("Profile URL") or row.get("linkedin_url") or ""
        ).strip() or None
        connected_on = (row.get("Connected On") or row.get("connected_on") or "").strip() or None
        email = (row.get("Email Address") or row.get("Email") or "").strip() or None
        out.append(
            {
                "name": name,
                "company": company,
                "linkedin_url": linkedin_url,
                "connected_on": connected_on,
                "email": email,
                "raw": dict(row),
            }
        )
    return out


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_mutuals(
    them_name: str,
    them_company: Optional[str],
    connections: Iterable[dict[str, Any]],
    *,
    limit: int = 8,
    name_threshold: float = 0.86,
) -> list[dict[str, Any]]:
    """Return connections that look like the same people-circle as THEM (shared network).

    Heuristic: connections whose company fuzzy-matches THEM's company, or whose names
    appear as known mutuals when Apollo org peers are later passed in. Never invents.
    """
    them_co = _norm(them_company or "")
    hits: list[tuple[float, dict]] = []
    for c in connections:
        score = 0.0
        reasons = []
        c_co = _norm(c.get("company") or "")
        if them_co and c_co:
            ratio = SequenceMatcher(None, them_co, c_co).ratio()
            if them_co in c_co or c_co in them_co or ratio >= 0.78:
                score += 0.55 + 0.2 * ratio
                reasons.append("same_or_similar_company")
        # Soft name overlap with them (rarely useful for mutuals — skip high self-match)
        name_ratio = SequenceMatcher(None, _norm(them_name), _norm(c.get("name") or "")).ratio()
        if name_ratio >= 0.92:
            continue  # that's THEM themselves
        if score >= 0.55:
            hits.append(
                (
                    score,
                    {
                        "name": c.get("name"),
                        "company": c.get("company"),
                        "linkedin_url": c.get("linkedin_url"),
                        "connected_on": c.get("connected_on"),
                        "evidence": reasons,
                        "score": round(score, 3),
                    },
                )
            )
    hits.sort(key=lambda x: -x[0])
    return [h[1] for h in hits[:limit]]


def find_person_in_connections(
    name: str,
    connections: Iterable[dict[str, Any]],
    *,
    company: Optional[str] = None,
    threshold: float = 0.88,
) -> Optional[dict[str, Any]]:
    """Check if THEM appears in the user's uploaded network."""
    target = _norm(name)
    best = None
    best_score = 0.0
    for c in connections:
        ratio = SequenceMatcher(None, target, _norm(c.get("name") or "")).ratio()
        if company and c.get("company"):
            if _norm(company) in _norm(c["company"]) or _norm(c["company"]) in _norm(company):
                ratio += 0.05
        if ratio > best_score:
            best_score = ratio
            best = c
    if best and best_score >= threshold:
        return {**best, "match_score": round(best_score, 3)}
    return None
