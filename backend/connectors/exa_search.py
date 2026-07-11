import os
from dataclasses import asdict
from typing import List, Optional

import requests

from connectors.reform_query import extract_and_evaluate_snippet, reformulate_query
from query_agent import run_goal_directed_search

EXA_API = "https://api.exa.ai/search"
TIMEOUT = 15


def search_person_exa(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
) -> dict:
    """Uses Exa's search API to reliably find a person's actual LinkedIn
    profile URL. The LinkedIn pass is goal-directed, not one-shot: if the
    first query doesn't turn up a confident linkedin.com/in/ match, a
    planner proposes a genuinely different next query instead of giving up.

    The general (non-LinkedIn) pass is driven by the reform_query agent:
    it rewrites the person lookup into a deeper semantic query + phrase
    filters, runs Exa with those, then extracts public mentions from the
    snippets. LinkedIn content beyond a bare profile link only ever comes
    through the dedicated linkedin_public connector — never here."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "EXA_API_KEY not set"}

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    def execute(query: str, include_domains: Optional[List[str]]) -> list:
        raw = _run_search(headers, query, include_domains=include_domains or ["linkedin.com"], num_results=8) or []
        return [r for r in raw if _is_profile_url(r.get("url"))]

    def success(results: list) -> bool:
        return len(results) > 0  # already filtered to genuine /in/ profile URLs only

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
    linkedin_url = linkedin_candidates[0]["url"] if linkedin_candidates else None

    # --- Deeper general pass via reform agent ---
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
        mention = extract_and_evaluate_snippet(snippet, result.get("url") or "")
        if mention:
            payload = asdict(mention)
            payload["url"] = result.get("url")
            payload["title"] = result.get("title")
            mentions.append(payload)

    found = bool(linkedin_url or general_results or mentions)
    return {
        "status": "ok" if found else "not_found",
        "linkedin_url": linkedin_url,
        "linkedin_search_attempts": agent_outcome["attempts"],
        "linkedin_candidates": linkedin_candidates,
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
