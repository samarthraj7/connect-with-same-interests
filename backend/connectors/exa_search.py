import os
import re
from dataclasses import asdict
from typing import List, Optional

import requests

from connectors.reform_query import extract_and_evaluate_snippet, reformulate_query
from query_agent import run_goal_directed_search

EXA_API = "https://api.exa.ai/search"
TIMEOUT = 15


def find_linkedin_people_by_name(
    name: str,
    *,
    company: Optional[str] = None,
    university: Optional[str] = None,
    distinguishable_factor: Optional[str] = None,
    max_people: int = 8,
) -> dict:
    """LinkedIn people discovery via Exa (linkedin.com/in/… results).

    When company/university are provided, those queries run first so Google-like
    disambiguation ("Name" "University of Southern California") actually affects
    who shows up — not only a post-hoc soft filter on a name-only shortlist.
    Soft hints (robotics, founder, startup…) boost discovery + ranking without
    hard-filtering out other exact-name matches.
    """
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "EXA_API_KEY not set", "candidates": []}

    name = (name or "").strip()
    if not name:
        return {"status": "error", "error": "name required", "candidates": []}

    company = (company or "").strip() or None
    university = (university or "").strip() or None
    hint = (distinguishable_factor or "").strip() or None

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    queries: List[str] = []
    if university:
        queries.extend(
            [
                f'"{name}" "{university}" site:linkedin.com/in',
                f'"{name}" {university} LinkedIn',
                f'"{name}" "{university}" alumni OR student OR MSCS OR graduate',
            ]
        )
        # Common short forms help when people write "USC" not the full name
        for alias in _org_aliases(university):
            if alias.lower() == university.lower():
                continue
            queries.append(f'"{name}" "{alias}" site:linkedin.com/in')
    if company and (not university or company.lower() != university.lower()):
        queries.extend(
            [
                f'"{name}" "{company}" site:linkedin.com/in',
                f'"{name}" {company} LinkedIn',
            ]
        )
        for alias in _org_aliases(company):
            if alias.lower() == company.lower():
                continue
            queries.append(f'"{name}" "{alias}" site:linkedin.com/in')
    # Soft distinguishable factor: bias search toward interests / role vibes
    if hint:
        for token in _hint_query_phrases(hint)[:4]:
            queries.extend(
                [
                    f'"{name}" "{token}" site:linkedin.com/in',
                    f'"{name}" {token} LinkedIn',
                ]
            )
    # Always finish with name-only so we still have a fallback shortlist
    queries.extend(
        [
            f'"{name}" site:linkedin.com/in',
            f'"{name}" LinkedIn',
            f"{name} site:linkedin.com/in",
            f"{name} LinkedIn profile",
        ]
    )
    # De-dupe queries while preserving order
    seen_q: set = set()
    ordered_queries = []
    for q in queries:
        key = q.lower()
        if key in seen_q:
            continue
        seen_q.add(key)
        ordered_queries.append(q)

    print(
        f"[exa linkedin-people] START name={name!r} company={company!r} "
        f"university={university!r} hint={hint!r}",
        flush=True,
    )
    seen: set = set()
    raw_hits: list = []
    # Prefer more hits when filters/hints are present — common names need room
    target = max_people * 3 if (company or university or hint) else max_people * 2

    for q in ordered_queries:
        if len(raw_hits) >= target:
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
            if "/in/" not in url:
                continue
            seen.add(url)
            raw_hits.append({**r, "url": url, "query": q})
            print(f"    → {url} | {(r.get('title') or '')[:80]}", flush=True)

    scored = []
    for h in raw_hits:
        cand = _hit_to_candidate(h, fallback_name=name)
        score = _name_overlap_score(name, cand.get("name") or "", h)
        score += _org_context_boost(h, cand, company=company, university=university)
        score += _hint_context_boost(h, cand, hint)
        # Prefer hits that came from a filtered query
        qtext = (h.get("query") or "").lower()
        if university and any(a.lower() in qtext for a in _org_aliases(university)):
            score += 0.2
        if company and any(a.lower() in qtext for a in _org_aliases(company)):
            score += 0.15
        if hint and any(t.lower() in qtext for t in _hint_query_phrases(hint)):
            score += 0.12
        cand["_score"] = min(score, 1.6)
        if hint:
            cand["_hint_score"] = _hint_match_score(cand, hint, hit=h)
        scored.append(cand)
        print(
            f"    score={cand['_score']:.2f} name={cand.get('name')!r} "
            f"co={cand.get('company')!r} li={cand.get('linkedin_url')}",
            flush=True,
        )

    ranked = sorted(scored, key=lambda x: (-x["_score"], -float(x.get("_hint_score") or 0)))
    keep_n = max(max_people * 2, max_people)
    if company or university or hint:
        keep_n = max(keep_n, 16)
    picked = [c for c in ranked if c["_score"] >= 0.25][:keep_n]
    if not picked and ranked:
        picked = ranked[:max_people]

    # If filters were set, surface org-matching people first even if name score was similar
    if company or university:
        matched = [c for c in picked if _candidate_matches_org(c, company=company, university=university)]
        others = [c for c in picked if c not in matched]
        if matched:
            picked = matched + others
    # Soft: hint-matching exact-ish rows float up without dropping others
    if hint:
        hinted = [c for c in picked if float(c.get("_hint_score") or 0) > 0]
        rest = [c for c in picked if c not in hinted]
        if hinted:
            picked = hinted + rest

    print(f"[exa linkedin-people] DONE count={len(picked)} (from {len(raw_hits)} hits)", flush=True)
    return {
        "status": "ok" if picked else "not_found",
        "candidates": picked,
        "source": "exa_linkedin_people",
        "filter_matched": sum(
            1 for c in picked if _candidate_matches_org(c, company=company, university=university)
        ),
        "hint_matched": sum(1 for c in picked if float(c.get("_hint_score") or 0) > 0) if hint else 0,
    }


_ORG_ALIAS_MAP = {
    "university of southern california": ["USC", "University of Southern California", "Trojans"],
    "usc": ["USC", "University of Southern California"],
    "massachusetts institute of technology": ["MIT", "Massachusetts Institute of Technology"],
    "mit": ["MIT", "Massachusetts Institute of Technology"],
    "stanford university": ["Stanford", "Stanford University"],
    "university of california berkeley": ["UC Berkeley", "Berkeley", "UCB"],
    "georgia institute of technology": ["Georgia Tech", "GT"],
    "carnegie mellon university": ["CMU", "Carnegie Mellon"],
    "university of california los angeles": ["UCLA", "University of California Los Angeles"],
}


def _org_aliases(org: Optional[str]) -> List[str]:
    raw = (org or "").strip()
    if not raw:
        return []
    out = [raw]
    key = re.sub(r"\s+", " ", raw.lower())
    mapped = _ORG_ALIAS_MAP.get(key)
    if mapped:
        for alias in mapped:
            if alias not in out:
                out.append(alias)
        return out
    # Unknown orgs: keep full string + meaningful tokens (skip filler words)
    stop = {
        "university",
        "college",
        "institute",
        "of",
        "the",
        "and",
        "at",
        "southern",
        "northern",
        "eastern",
        "western",
        "state",
    }
    for tok in re.findall(r"[A-Za-z][A-Za-z&.-]{2,}", raw):
        if tok.lower() in stop:
            continue
        if len(tok) < 4:
            continue
        if tok not in out:
            out.append(tok)
    return out


def _blob_for_candidate(hit: Optional[dict], cand: Optional[dict]) -> str:
    hit = hit or {}
    cand = cand or {}
    return " ".join(
        [
            cand.get("name") or "",
            cand.get("company") or "",
            cand.get("role") or "",
            cand.get("location") or "",
            cand.get("context") or "",
            hit.get("title") or "",
            (hit.get("text_snippet") or "")[:500],
            hit.get("url") or "",
        ]
    ).lower()


def _org_context_boost(
    hit: dict,
    cand: dict,
    *,
    company: Optional[str],
    university: Optional[str],
) -> float:
    blob = _blob_for_candidate(hit, cand)
    boost = 0.0
    if university:
        for alias in _org_aliases(university):
            if alias.lower() in blob:
                boost += 0.4
                break
    if company:
        for alias in _org_aliases(company):
            if alias.lower() in blob:
                boost += 0.3
                break
    return boost


def _hint_query_phrases(hint: Optional[str]) -> List[str]:
    """Turn 'robotics, founder, startup' into searchable phrases."""
    raw = (hint or "").strip()
    if not raw:
        return []
    parts = re.split(r"[,;/|]+|\s{2,}", raw)
    out: List[str] = []
    seen = set()
    for p in parts:
        p = re.sub(r"\s+", " ", p.strip())
        if len(p) < 2:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    if not out and raw:
        out.append(raw)
    # Also keep meaningful single tokens from the whole string
    stop = {"and", "the", "or", "a", "an", "into", "for", "with", "who", "is", "are"}
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9&.-]{2,}", raw):
        if tok.lower() in stop:
            continue
        if tok.lower() not in seen:
            seen.add(tok.lower())
            out.append(tok)
    return out[:8]


def _hint_context_boost(hit: Optional[dict], cand: Optional[dict], hint: Optional[str]) -> float:
    if not hint:
        return 0.0
    blob = _blob_for_candidate(hit, cand)
    boost = 0.0
    for phrase in _hint_query_phrases(hint):
        pl = phrase.lower()
        if pl in blob:
            boost += 0.35 if " " in pl or len(pl) > 6 else 0.22
    return min(boost, 0.7)


def _hint_match_score(cand: Optional[dict], hint: Optional[str], *, hit: Optional[dict] = None) -> float:
    """0..1-ish score for how well a candidate card matches the soft hint."""
    if not hint or not cand:
        return 0.0
    blob = _blob_for_candidate(hit, cand)
    phrases = _hint_query_phrases(hint)
    if not phrases:
        return 0.0
    hits = sum(1 for p in phrases if p.lower() in blob)
    if not hits:
        return 0.0
    return min(1.0, hits / max(1, min(3, len(phrases))) + (0.15 if hits >= 2 else 0))


def _candidate_matches_org(
    cand: dict,
    *,
    company: Optional[str] = None,
    university: Optional[str] = None,
) -> bool:
    blob = _blob_for_candidate(None, cand)
    if university:
        if any(a.lower() in blob for a in _org_aliases(university)):
            return True
    if company:
        if any(a.lower() in blob for a in _org_aliases(company)):
            return True
    return False


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
    search_constraints: Optional[dict] = None,
) -> dict:
    """Uses Exa's search API to find LinkedIn + public mentions.

    When linkedin_url is already known (Find Me pick), it is treated as hard
    truth — we do NOT rediscover a different /in/ profile.
    """
    from identity_lock import linkedin_slug, normalize_linkedin_url

    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "EXA_API_KEY not set"}

    sc = search_constraints or {}
    company = sc.get("prefer_company") or company
    university = sc.get("prefer_university") or university
    reject_slugs = {s.lower() for s in (sc.get("reject_slugs") or []) if s}
    exclude_domains = list(sc.get("reject_domains") or [])

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
    if sc.get("query_suffix"):
        general_query = f"{general_query} {sc['query_suffix']}".strip()
        print(f"  [exa] feedback query_suffix applied", flush=True)

    exclude_domains = list(dict.fromkeys(["linkedin.com"] + exclude_domains))
    general_results = _run_search(
        headers,
        general_query,
        num_results=8,
        want_text=True,
        phrase_filters=phrase_filters,
        exclude_domains=exclude_domains,
    )

    mentions = []
    for result in general_results or []:
        snippet = result.get("text_snippet") or result.get("title") or ""
        # Soft identity filter: if we have company, prefer snippets that mention it or the slug
        blob = f"{snippet} {result.get('url') or ''} {result.get('title') or ''}".lower()
        if reject_slugs and any(s in blob for s in reject_slugs):
            continue
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
