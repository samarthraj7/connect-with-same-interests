"""Public web + portfolio discovery (Exa + optional domain filters).

Looks beyond LinkedIn for personal sites, GitHub Pages, Notion, Behance, etc.
"""

from __future__ import annotations

import os
import re
from typing import Any, List, Optional

import requests

EXA_API = "https://api.exa.ai/search"
TIMEOUT = 20

PORTFOLIO_DOMAINS = [
    "github.io",
    "notion.site",
    "notion.so",
    "behance.net",
    "dribbble.com",
    "carbonmade.com",
    "about.me",
    "read.cv",
    "carrd.co",
    "webflow.io",
    "framer.website",
    "medium.com",
    "substack.com",
    "dev.to",
]


def search_public_presence(
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> dict[str, Any]:
    from identity_lock import linkedin_slug, normalize_linkedin_url

    api_key = (os.environ.get("EXA_API_KEY") or "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "EXA_API_KEY not set"}

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    slug = linkedin_slug(normalize_linkedin_url(linkedin_url))
    base = f"{name} {company or university or ''}".strip()
    if slug:
        base = f"{base} {slug}".strip()

    queries = [
        f"{base} personal website OR portfolio OR homepage -site:linkedin.com",
        f"{base} github.io OR read.cv OR about.me OR behance",
        f"{base} blog OR newsletter OR talks OR speaking",
    ]

    print(f"  [public_web] portfolio / open-web dig for {name!r} (slug={slug!r})")
    all_hits: List[dict] = []
    seen = set()
    for q in queries:
        hits = _exa(headers, q, num_results=6) or []
        for h in hits:
            url = (h.get("url") or "").rstrip("/")
            if not url or url in seen:
                continue
            if "linkedin.com" in url:
                continue
            seen.add(url)
            all_hits.append(h)

    portfolios = [h for h in all_hits if _looks_portfolio(h)]
    blogs = [h for h in all_hits if _looks_writing(h) and h not in portfolios]
    other = [h for h in all_hits if h not in portfolios and h not in blogs]

    found = bool(all_hits)
    return {
        "status": "ok" if found else "not_found",
        "portfolios": portfolios[:8],
        "writing_and_talks": blogs[:8],
        "other_public_pages": other[:8],
        "count": len(all_hits),
        "canonical_linkedin_url": normalize_linkedin_url(linkedin_url),
    }


def _looks_portfolio(hit: dict) -> bool:
    url = (hit.get("url") or "").lower()
    title = (hit.get("title") or "").lower()
    snippet = (hit.get("text_snippet") or "").lower()
    blob = f"{url} {title} {snippet}"
    if any(d in url for d in PORTFOLIO_DOMAINS):
        return True
    return bool(re.search(r"\b(portfolio|personal site|homepage|my work|case stud)\b", blob))


def _looks_writing(hit: dict) -> bool:
    url = (hit.get("url") or "").lower()
    title = (hit.get("title") or "").lower()
    return any(
        x in url or x in title
        for x in ("substack", "medium.com", "blog", "talk", "speaker", "interview", "newsletter", "dev.to")
    )


def _exa(headers: dict, query: str, num_results: int = 5) -> Optional[List[dict]]:
    body = {
        "query": query,
        "type": "auto",
        "numResults": num_results,
        "contents": {"text": {"maxCharacters": 800}},
        "excludeDomains": ["linkedin.com"],
    }
    try:
        resp = requests.post(EXA_API, headers=headers, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        return [
            {
                "url": r.get("url"),
                "title": r.get("title"),
                "published_date": r.get("publishedDate"),
                "text_snippet": (r.get("text") or "")[:400] or None,
            }
            for r in resp.json().get("results", [])
        ]
    except requests.RequestException:
        return None
