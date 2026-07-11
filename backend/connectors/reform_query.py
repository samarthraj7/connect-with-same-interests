"""Query-reformulation agent: rewrites a person search into deeper Exa /
keyword queries, then extracts public mentions from the results.

Used by exa_search for the general (non-LinkedIn) pass so we don't just
search "{name} {company}" once and stop.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional

import requests
from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
EXA_API = "https://api.exa.ai/search"
TIMEOUT = 15

REFORM_PROMPT = """You are an expert search-query planning agent. Rewrite a \
person lookup into queries that dig deeper than a bare name search — targeting \
public mentions, talks, interviews, personal sites, press, social posts they \
authored, and senior colleagues (CEO/CTO/CFO/VP) at their company.

PERSON: {name}
COMPANY: {company}
UNIVERSITY: {university}
PLACE: {place}

Return strict JSON only, no markdown fences:
{{
  "exa_semantic_query": string,
  "google_keyword_query": string,
  "phrase_filters": [string]
}}

Rules:
- exa_semantic_query: natural-language, written the way an author or journalist \
  would describe this person's public writing, talks, or posts (role, company, \
  topics they discuss). Optimized for semantic search.
- google_keyword_query: traditional operators (quotes, OR, site: for personal \
  sites / news / talks / blogs). Do NOT use site:linkedin.com — LinkedIn is \
  handled by a separate connector.
- phrase_filters: 1–4 short exact substrings likely to appear in real pages \
  about this person (name parts, company, distinctive role words). Keep them \
  short; empty list is fine if nothing distinctive is known.
"""

EXTRACT_PROMPT = """You are a raw text ingestion engine. Analyze the search \
snippet or webpage highlight about a person. Determine if it contains a real \
public mention, post, interview quote, bio blurb, or press fragment.

If it is unrelated boilerplate (cookie policy, nav, directory chrome), set \
relevance_score to 0. Otherwise extract details carefully.

Respond with strict JSON only, no markdown fences:
{{
  "platform": string,
  "author": string or null,
  "post_text": string,
  "timestamp": string or null,
  "relevance_score": number
}}
"""


@dataclass
class ReformulatedQueries:
    exa_semantic_query: str
    google_keyword_query: str
    phrase_filters: List[str] = field(default_factory=list)


@dataclass
class ExtractedMention:
    platform: str
    post_text: str
    relevance_score: float
    author: Optional[str] = None
    timestamp: Optional[str] = None


def reformulate_query(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
) -> Optional[ReformulatedQueries]:
    """Agent step: turn a person intent into Exa + keyword queries for a deeper search."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    prompt = REFORM_PROMPT.format(
        name=name,
        company=company or "(unknown)",
        university=university or "(unknown)",
        place=place or "(unknown)",
    )

    try:
        client = genai.Client(api_key=api_key)
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text or "")
        return ReformulatedQueries(
            exa_semantic_query=(data.get("exa_semantic_query") or "").strip(),
            google_keyword_query=(data.get("google_keyword_query") or "").strip(),
            phrase_filters=[p for p in (data.get("phrase_filters") or []) if isinstance(p, str) and p.strip()],
        )
    except (errors.ClientError, errors.ServerError, json.JSONDecodeError, TypeError, ValueError):
        return None


def extract_and_evaluate_snippet(snippet_text: str, source_url: str) -> Optional[ExtractedMention]:
    """Gatekeeper LLM: keep only snippets that look like real public mentions."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not snippet_text.strip():
        return None

    try:
        client = genai.Client(api_key=api_key)
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=f"{EXTRACT_PROMPT}\n\nURL Context: {source_url}\n\nSnippet Content:\n{snippet_text}",
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text or "")
        score = float(data.get("relevance_score") or 0)
        if score <= 0.6:
            return None
        text = (data.get("post_text") or "").strip()
        if not text:
            return None
        return ExtractedMention(
            platform=(data.get("platform") or "web").strip(),
            author=data.get("author"),
            post_text=text,
            timestamp=data.get("timestamp"),
            relevance_score=score,
        )
    except (errors.ClientError, errors.ServerError, json.JSONDecodeError, TypeError, ValueError):
        return None


def deep_search_person(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
) -> dict:
    """Full reform → Exa → extract pipeline. Connector-shaped return value."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "EXA_API_KEY not set"}

    print(f"  [reform agent] rewriting query for deeper search: {name!r}")
    optimized = reformulate_query(name, company=company, university=university, place=place)
    if optimized is None or not optimized.exa_semantic_query:
        # Fall back to a plain query so missing Gemini doesn't kill the connector
        fallback = f"{name} {company or ''} {university or ''} {place or ''}".strip()
        optimized = ReformulatedQueries(
            exa_semantic_query=fallback,
            google_keyword_query=fallback,
            phrase_filters=[],
        )
        print("  [reform agent] reformulation unavailable — using baseline query")
    else:
        print(f"  [reform agent] exa: {optimized.exa_semantic_query!r}")
        print(f"  [reform agent] keyword: {optimized.google_keyword_query!r}")
        if optimized.phrase_filters:
            print(f"  [reform agent] phrase filters: {optimized.phrase_filters}")

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    raw_results = _exa_search(
        headers,
        optimized.exa_semantic_query,
        phrase_filters=optimized.phrase_filters,
        num_results=6,
        exclude_domains=["linkedin.com"],
    ) or []

    mentions = []
    for result in raw_results:
        snippet = result.get("text_snippet") or result.get("title") or ""
        mention = extract_and_evaluate_snippet(snippet, result.get("url") or "")
        if mention:
            payload = asdict(mention)
            payload["url"] = result.get("url")
            payload["title"] = result.get("title")
            mentions.append(payload)

    found = bool(raw_results or mentions)
    return {
        "status": "ok" if found else "not_found",
        "reformulated": asdict(optimized),
        "search_results": raw_results,
        "mentions": mentions,
    }


def _exa_search(
    headers: dict,
    query: str,
    phrase_filters: Optional[List[str]] = None,
    num_results: int = 5,
    exclude_domains: Optional[List[str]] = None,
) -> Optional[List[dict]]:
    body = {
        "query": query,
        "type": "auto",
        "numResults": num_results,
        "contents": {"text": {"maxCharacters": 1000}},
    }
    if phrase_filters:
        # Exa includeText: each string must appear; keep filters short
        body["includeText"] = [p[:64] for p in phrase_filters[:5]]
    if exclude_domains:
        body["excludeDomains"] = exclude_domains

    try:
        resp = requests.post(EXA_API, headers=headers, json=body, timeout=TIMEOUT)
        # If includeText is too strict and Exa rejects, retry without filters
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
