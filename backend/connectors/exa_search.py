import os
import re
from dataclasses import asdict
from typing import List, Optional

import requests

from connectors.reform_query import extract_and_evaluate_snippet, reformulate_query
from query_agent import run_goal_directed_search

EXA_API = "https://api.exa.ai/search"
TIMEOUT = 15


def find_linkedin_people_by_name(name: str, *, max_people: int = 8) -> dict:
    """Name-only LinkedIn people discovery via Exa (linkedin.com/in/… results).

    No company / university / LinkedIn URL required — this is the Find Me
    primary path: web search restricted to LinkedIn profile pages.
    """
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "EXA_API_KEY not set", "candidates": []}

    name = (name or "").strip()
    if not name:
        return {"status": "error", "error": "name required", "candidates": []}

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    queries = [
        f'"{name}" site:linkedin.com/in',
        f'"{name}" LinkedIn',
        f"{name} site:linkedin.com/in",
        f"{name} LinkedIn profile",
    ]

    print(f"[exa linkedin-people] START name={name!r}", flush=True)
    seen: set = set()
    raw_hits: list = []

    for q in queries:
        if len(raw_hits) >= max_people * 2:
            break
        print(f"  [exa linkedin-people] query={q!r}", flush=True)
        results = _run_search(
            headers,
            q,
            include_domains=["linkedin.com"],
            num_results=10,
            want_text=True,
        )
        for r in results or []:
            url = _canonical_profile_url(r.get("url"))
            if not url or url in seen:
                continue
            # Skip company/school/pulse pages that still match include_domains
            if "/in/" not in url:
                continue
            seen.add(url)
            raw_hits.append({**r, "url": url, "query": q})
            print(f"    → {url} | {(r.get('title') or '')[:80]}", flush=True)

    scored = []
    for h in raw_hits:
        cand = _hit_to_candidate(h, fallback_name=name)
        score = _name_overlap_score(name, cand.get("name") or "", h)
        cand["_score"] = score
        scored.append(cand)
        print(f"    score={score:.2f} name={cand.get('name')!r} li={cand.get('linkedin_url')}", flush=True)

    # Prefer strong name matches; pad with medium for disambiguation UI
    strong = sorted([c for c in scored if c["_score"] >= 0.45], key=lambda x: -x["_score"])
    medium = sorted([c for c in scored if 0.25 <= c["_score"] < 0.45], key=lambda x: -x["_score"])
    picked = strong[:max_people]
    if len(picked) < min(3, max_people):
        picked.extend(medium[: max_people - len(picked)])
    if not picked:
        picked = sorted(scored, key=lambda x: -x["_score"])[:max_people]
    for c in picked:
        c.pop("_score", None)

    print(f"[exa linkedin-people] DONE count={len(picked)} (from {len(raw_hits)} hits)", flush=True)
    return {
        "status": "ok" if picked else "not_found",
        "candidates": picked,
        "source": "exa_linkedin_people",
    }


def _canonical_profile_url(url: Optional[str]) -> Optional[str]:
    if not url or "linkedin.com/in/" not in url.lower():
        return None
    base = url.split("?")[0].split("#")[0].rstrip("/")
    # Normalize country subdomains → www
    base = re.sub(r"https?://([a-z]{2}\.)?linkedin\.com", "https://www.linkedin.com", base, flags=re.I)
    return base


def _tokens(s: str) -> list:
    return [t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) > 1 and not re.fullmatch(r"[a-f0-9]{6,}", t)]


def _name_overlap_score(query_name: str, candidate_name: str, hit: dict) -> float:
    q = set(_tokens(query_name))
    if not q:
        return 0.0
    blob = " ".join(
        [
            candidate_name or "",
            hit.get("title") or "",
            (hit.get("text_snippet") or "")[:300],
            hit.get("url") or "",
        ]
    )
    b = set(_tokens(blob))
    if not b:
        return 0.0
    overlap = len(q & b) / len(q)
    # Bonus if first+last both present
    qlist = _tokens(query_name)
    if len(qlist) >= 2 and qlist[0] in b and qlist[-1] in b:
        overlap = max(overlap, 0.85)
    return overlap


def _hit_to_candidate(hit: dict, *, fallback_name: str) -> dict:
    """Parse LinkedIn search title/snippet into a Find Me candidate card."""
    title = (hit.get("title") or "").strip()
    snippet = (hit.get("text_snippet") or "").strip()
    url = hit.get("url") or ""

    person_name = None
    role = None
    company = None
    location = None
    context = None

    # Snippet from Exa often looks like markdown:
    # "# Rhesha Vinod\n\nSDE Intern@Sprintray | MSCS @ USC | …"
    if snippet:
        m = re.search(r"^#\s*(.+)$", snippet, re.M)
        if m:
            person_name = m.group(1).strip()
        lines = [ln.strip() for ln in snippet.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if lines:
            headline = lines[0]
            context = headline[:220]
            # "SDE Intern@Sprintray | MSCS @ USC"
            if "@" in headline and "|" in headline:
                left = headline.split("|")[0].strip()
                if "@" in left:
                    role_part, _, co = left.partition("@")
                    role = role_part.strip() or None
                    company = co.strip() or None
            elif " at " in headline.lower():
                bits = re.split(r"\s+at\s+", headline, maxsplit=1, flags=re.I)
                if len(bits) == 2:
                    role, company = bits[0].strip(), bits[1].strip()
            # Location line: "Chennai, Tamil Nadu, India (IN)"
            for ln in lines[1:4]:
                if re.search(r"\b(India|United States|USA|UK|Canada)\b", ln, re.I) or "(IN)" in ln or "(US)" in ln:
                    location = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", ln).strip()
                    break

    # Title patterns: "rhesha vinod | Software Engineer Intern"
    clean = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title, flags=re.I)
    clean = re.sub(r"\s*-\s*LinkedIn\s*$", "", clean, flags=re.I).strip()
    if "|" in clean:
        left, right = [p.strip() for p in clean.split("|", 1)]
        if not person_name and left:
            person_name = left
        if not role and right and "linkedin" not in right.lower():
            role = right
    elif " - " in clean or " – " in clean:
        m = re.match(r"^(.+?)\s*[-–—]\s*(.+?)\s+at\s+(.+)$", clean, flags=re.I)
        if m:
            person_name = person_name or m.group(1).strip()
            role = role or m.group(2).strip()
            company = company or m.group(3).strip()
        else:
            parts = re.split(r"\s*[-–—]\s*", clean, maxsplit=1)
            if len(parts) == 2:
                person_name = person_name or parts[0].strip()
                rest = parts[1].strip()
                at_parts = re.split(r"\s+at\s+", rest, maxsplit=1, flags=re.I)
                if len(at_parts) == 2:
                    role = role or at_parts[0].strip() or None
                    company = company or at_parts[1].strip() or None
                else:
                    role = role or rest or None

    if not person_name:
        person_name = fallback_name

    # Title-case messy all-lowercase LinkedIn titles
    if person_name.islower() or person_name.isupper():
        person_name = " ".join(w.capitalize() for w in person_name.split())

    linkedin = url if url.startswith("http") else f"https://{url}"
    linkedin = _canonical_profile_url(linkedin) or linkedin

    return {
        "name": person_name,
        "company": company,
        "role": role,
        "location": location,
        "linkedin_url": linkedin,
        "photo_url": None,
        "context": context or (snippet[:200] if snippet else f"LinkedIn profile for {person_name}"),
        "source": "exa_linkedin_people",
    }


def search_person_exa(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> dict:
    """Uses Exa's search API to find LinkedIn + public mentions.

    When linkedin_url is already known (Find Me pick), it is treated as hard
    truth — we do NOT rediscover a different /in/ profile.
    """
    from identity_lock import linkedin_slug, normalize_linkedin_url

    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "EXA_API_KEY not set"}

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    canonical = normalize_linkedin_url(linkedin_url)
    slug = linkedin_slug(canonical)

    if canonical:
        print(f"  [exa] using canonical LinkedIn {canonical} (skip rediscovery)", flush=True)
        linkedin_candidates = [{"url": canonical, "title": name, "text_snippet": None}]
        linkedin_url_resolved = canonical
        agent_outcome = {"attempts": [{"query": "canonical", "success": True}], "result": linkedin_candidates}
    else:
        def execute(query: str, include_domains: Optional[List[str]]) -> list:
            raw = _run_search(headers, query, include_domains=include_domains or ["linkedin.com"], num_results=8) or []
            return [r for r in raw if _is_profile_url(r.get("url"))]

        def success(results: list) -> bool:
            return len(results) > 0

        goal = f"Find the exact LinkedIn profile URL (linkedin.com/in/...) for {name}"
        if company:
            goal += f", who works at {company}"

        agent_outcome = run_goal_directed_search(
            goal=goal,
            context={"name": name, "company": company, "university": university, "place": place},
            execute_query=execute,
            check_success=success,
            initial_query=f"{name} {company or ''} LinkedIn profile".strip(),
        )

        linkedin_candidates = agent_outcome["result"]
        linkedin_url_resolved = linkedin_candidates[0]["url"] if linkedin_candidates else None
        if linkedin_url_resolved:
            linkedin_url_resolved = normalize_linkedin_url(linkedin_url_resolved) or linkedin_url_resolved

    # --- Deeper general pass via reform agent (anchored when LI known) ---
    print(f"  [reform agent] rewriting general Exa query for {name!r}")
    optimized = reformulate_query(name, company=company, university=university, place=place)
    if optimized and optimized.exa_semantic_query:
        general_query = optimized.exa_semantic_query
        phrase_filters = optimized.phrase_filters
        print(f"  [reform agent] exa: {general_query!r}")
        if phrase_filters:
            print(f"  [reform agent] phrase filters: {phrase_filters}")
        reformulated = asdict(optimized)
    else:
        general_query = f"{name} {company or ''} {university or ''} {place or ''}".strip()
        phrase_filters = []
        reformulated = None
        print("  [reform agent] unavailable — falling back to baseline general query")

    if slug:
        general_query = f"{general_query} {slug}".strip()
    if company:
        general_query = f"{general_query} {company}".strip()

    general_results = _run_search(
        headers,
        general_query,
        num_results=8,
        want_text=True,
        phrase_filters=phrase_filters,
        exclude_domains=["linkedin.com"],
    )

    mentions = []
    for result in general_results or []:
        snippet = result.get("text_snippet") or result.get("title") or ""
        # Soft identity filter: if we have company, prefer snippets that mention it or the slug
        blob = f"{snippet} {result.get('url') or ''} {result.get('title') or ''}".lower()
        if company and company.lower() not in blob and slug and slug not in blob:
            # Still keep if name appears strongly
            name_tokens = [t for t in name.lower().split() if len(t) > 2]
            if not all(t in blob for t in name_tokens[:2]):
                continue
        mention = extract_and_evaluate_snippet(snippet, result.get("url") or "")
        if mention:
            payload = asdict(mention)
            payload["url"] = result.get("url")
            payload["title"] = result.get("title")
            mentions.append(payload)

    found = bool(linkedin_url_resolved or general_results or mentions)
    return {
        "status": "ok" if found else "not_found",
        "linkedin_url": linkedin_url_resolved,
        "linkedin_search_attempts": agent_outcome["attempts"],
        "linkedin_candidates": linkedin_candidates,
        "canonical_linkedin_url": canonical,
        "reformulated": reformulated,
        "general_results": general_results or [],
        "mentions": mentions,
    }


def _is_profile_url(url: Optional[str]) -> bool:
    return bool(url) and "linkedin.com/in/" in url


def _run_search(
    headers: dict,
    query: str,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    phrase_filters: Optional[List[str]] = None,
    num_results: int = 5,
    want_text: bool = False,
) -> Optional[List[dict]]:
    body = {"query": query, "type": "auto", "numResults": num_results}
    if include_domains:
        body["includeDomains"] = include_domains
    if exclude_domains:
        body["excludeDomains"] = exclude_domains
    if phrase_filters:
        body["includeText"] = [p[:64] for p in phrase_filters[:5]]
    if want_text:
        body["contents"] = {"text": {"maxCharacters": 1000}}

    try:
        resp = requests.post(EXA_API, headers=headers, json=body, timeout=TIMEOUT)
        # Phrase filters can be too strict — retry once without them
        if resp.status_code >= 400 and phrase_filters:
            body.pop("includeText", None)
            resp = requests.post(EXA_API, headers=headers, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [
            {
                "url": r.get("url"),
                "title": r.get("title"),
                "published_date": r.get("publishedDate"),
                "text_snippet": (r.get("text") or "")[:500] or None,
            }
            for r in results
        ]
    except requests.RequestException:
        return None
