"""Agentic deep search — plan → retrieve → page-fetch → extract → critique (max hops).

Runs after identity is chosen (name + LinkedIn). Public web only; identity-locked.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry
from identity_lock import identity_lock_text, normalize_linkedin_url, same_linkedin

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
MAX_HOPS = int(os.environ.get("DEEP_AGENT_MAX_HOPS") or "2")
MAX_URLS_PER_HOP = 6
FETCH_TIMEOUT = 8
MAX_PAGE_CHARS = 4000
HEADERS = {"User-Agent": "ConnectDeeplyBot/0.2 (+public research)"}


def run_deep_agent(
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    seed_sources: Optional[dict] = None,
) -> dict[str, Any]:
    """Multi-hop public research into an evidence bag for synthesize."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    canonical = normalize_linkedin_url(linkedin_url)
    identity = {
        "name": name,
        "company": company,
        "university": university,
        "place": place,
        "linkedin_url": canonical or linkedin_url,
    }
    evidence: list[dict] = []
    search_trail: list[dict] = []
    known = _seed_known(seed_sources or {})

    print(
        f"  [deep_agent] START name={name!r} li={bool(canonical)} hops={MAX_HOPS}",
        flush=True,
    )
    client = genai.Client(api_key=api_key)

    for hop in range(1, MAX_HOPS + 1):
        goals = _plan_goals(client, identity=identity, known=known, hop=hop)
        search_trail.append({"hop": hop, "goals": goals})
        print(f"  [deep_agent] hop {hop}/{MAX_HOPS} goals={len(goals)}", flush=True)
        if not goals:
            break

        urls: list[str] = []
        for g in goals[:5]:
            q = (g.get("query") or "").strip()
            if not q:
                continue
            found = _retrieve_urls(q, identity=identity)
            urls.extend(found)
        urls = _dedupe_urls(urls)[:MAX_URLS_PER_HOP]

        pages = _fetch_pages(urls)
        extracted = _extract_facts(client, identity=identity, pages=pages, known=known)
        for item in extracted:
            if not isinstance(item, dict):
                continue
            fact = (item.get("fact") or "").strip()
            src = (item.get("source_url") or "").strip()
            if not fact:
                continue
            evidence.append(
                {
                    "fact": fact,
                    "source_url": src or None,
                    "category": item.get("category") or "general",
                    "hop": hop,
                }
            )
            known.setdefault("facts", []).append(fact)

        critique = _critique_gaps(client, identity=identity, known=known, evidence=evidence)
        search_trail[-1]["critique"] = critique.get("summary")
        if not critique.get("needs_more"):
            print(f"  [deep_agent] hop {hop} critique: enough evidence", flush=True)
            break
        known["next_queries"] = critique.get("next_queries") or []

    socials = _discover_socials_once(
        name=name,
        company=company,
        linkedin_url=canonical or linkedin_url,
    )

    status = "ok" if evidence or socials.get("found") else "not_found"
    print(
        f"  [deep_agent] DONE status={status} evidence={len(evidence)} "
        f"socials={socials.get('found')}",
        flush=True,
    )
    return {
        "status": status,
        "identity": identity,
        "evidence": evidence[:40],
        "social_profile_links": socials.get("links") or {},
        "social_discovery": socials,
        "search_trail": search_trail,
        "hops_run": len(search_trail),
    }


def _seed_known(sources: dict) -> dict:
    known: dict[str, Any] = {"facts": [], "roles": [], "urls": []}
    gemini = sources.get("gemini_search") if isinstance(sources.get("gemini_search"), dict) else {}
    apollo = sources.get("apollo") if isinstance(sources.get("apollo"), dict) else {}
    if apollo.get("title"):
        known["roles"].append(apollo["title"])
    if gemini.get("current_role"):
        known["roles"].append(gemini["current_role"])
    for item in (gemini.get("career_history") or [])[:8]:
        if isinstance(item, str):
            known["facts"].append(item)
        elif isinstance(item, dict) and item.get("title"):
            known["facts"].append(str(item.get("title")))
    return known


def _plan_goals(client, *, identity: dict, known: dict, hop: int) -> list[dict]:
    lock = identity_lock_text(
        name=identity.get("name"),
        linkedin_url=identity.get("linkedin_url"),
        company=identity.get("company"),
        university=identity.get("university"),
    )
    prompt = f"""{lock}

You plan PUBLIC web search queries for a pre-meeting briefing.
Hop {hop}. Prefer queries that find career, education, writing, awards, personal site.
Do NOT invent LinkedIn URLs. Do NOT target login-gated feeds.

Identity JSON: {json.dumps(identity)}
Already known: {json.dumps({k: known.get(k) for k in ('facts','roles','next_queries')}, default=str)[:3000]}

Return JSON only:
{{"goals":[{{"query":string,"focus":string}}]}}
Max 4 goals. Each query must include the person's name.
"""
    try:
        resp = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(resp.text or "{}")
        goals = data.get("goals") if isinstance(data, dict) else None
        if isinstance(goals, list):
            return [g for g in goals if isinstance(g, dict)][:4]
    except (errors.ClientError, errors.ServerError, json.JSONDecodeError, TypeError) as exc:
        print(f"  [deep_agent] plan error: {exc}", flush=True)
    name = identity.get("name") or ""
    co = identity.get("company") or ""
    return [
        {"query": f'"{name}" {co} career OR biography'.strip(), "focus": "career"},
        {"query": f'"{name}" {co} interview OR talk OR blog'.strip(), "focus": "writing"},
    ]


def _retrieve_urls(query: str, *, identity: dict) -> list[str]:
    urls: list[str] = []
    try:
        from connectors import exa_search

        api_key = os.environ.get("EXA_API_KEY")
        if api_key:
            headers = {"x-api-key": api_key, "Content-Type": "application/json"}
            results = exa_search._run_search(  # noqa: SLF001
                headers,
                query,
                num_results=5,
                want_text=True,
                exclude_domains=None,
            )
            for r in results or []:
                u = (r.get("url") or "").strip()
                if u:
                    urls.append(u)
    except Exception as exc:
        print(f"  [deep_agent] exa retrieve: {exc}", flush=True)

    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            lock = identity_lock_text(
                name=identity.get("name"),
                linkedin_url=identity.get("linkedin_url"),
                company=identity.get("company"),
            )
            resp = generate_with_retry(
                client,
                model=MODEL,
                contents=f"{lock}\n\nSearch the public web for: {query}\nList facts with source URLs.",
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                ),
            )
            for u in _grounding_urls(resp):
                urls.append(u)
        except Exception as exc:
            print(f"  [deep_agent] gemini retrieve: {exc}", flush=True)

    canonical = normalize_linkedin_url(identity.get("linkedin_url"))
    if canonical:
        from identity_filter import url_conflicts_with_canonical

        filtered = []
        for u in urls:
            if url_conflicts_with_canonical(u, canonical):
                print(f"  [deep_agent] drop URL (other LinkedIn identity): {u}", flush=True)
                continue
            filtered.append(u)
        urls = filtered
    return urls


def _grounding_urls(response) -> list[str]:
    out = []
    try:
        for cand in response.candidates or []:
            meta = getattr(cand, "grounding_metadata", None)
            chunks = getattr(meta, "grounding_chunks", None) or []
            for ch in chunks:
                web = getattr(ch, "web", None)
                uri = getattr(web, "uri", None) if web else None
                if uri:
                    out.append(uri)
    except Exception:
        pass
    return out


def _fetch_pages(urls: list[str]) -> list[dict]:
    pages: list[dict] = []

    # Prefer Nimble when configured — full page markdown beats OG tags / raw GET.
    try:
        from connectors import nimble

        if nimble.configured():
            result = nimble.extract_many(urls, max_pages=min(len(urls), MAX_URLS_PER_HOP))
            blobs = nimble.pages_as_text_blobs(result, max_chars=MAX_PAGE_CHARS)
            if blobs:
                return blobs
            print("  [deep_agent] Nimble returned no pages — falling back to OG/GET", flush=True)
    except Exception as exc:
        print(f"  [deep_agent] Nimble unavailable ({exc}) — falling back", flush=True)

    def one(url: str) -> Optional[dict]:
        try:
            from connectors import opengraph

            og = opengraph.fetch_open_graph(url)
            text = ""
            title = og.get("title") if isinstance(og, dict) else None
            desc = og.get("description") if isinstance(og, dict) else None
            if title or desc:
                text = f"{title or ''}\n{desc or ''}".strip()
            if len(text) < 80:
                resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT, allow_redirects=True)
                if resp.ok and "text/html" in (resp.headers.get("content-type") or ""):
                    raw = re.sub(r"<script[\s\S]*?</script>", " ", resp.text, flags=re.I)
                    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
                    raw = re.sub(r"<[^>]+>", " ", raw)
                    raw = re.sub(r"\s+", " ", raw).strip()
                    text = (text + "\n" + raw).strip()[:MAX_PAGE_CHARS]
            if not text:
                return None
            return {"url": url, "text": text[:MAX_PAGE_CHARS]}
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(one, u): u for u in urls}
        for fut in as_completed(futs):
            page = fut.result()
            if page:
                pages.append(page)
    return pages


def _extract_facts(client, *, identity: dict, pages: list[dict], known: dict) -> list[dict]:
    if not pages:
        return []
    lock = identity_lock_text(
        name=identity.get("name"),
        linkedin_url=identity.get("linkedin_url"),
        company=identity.get("company"),
    )
    blob = json.dumps(pages[:MAX_URLS_PER_HOP], ensure_ascii=False)[:12000]
    prompt = f"""{lock}

Extract ONLY facts about the selected person from these page texts.
Discard other same-name people. Each fact needs a source_url from the pages.
Return JSON:
{{"facts":[{{"fact":string,"source_url":string,"category":"career"|"education"|"writing"|"personal"|"award"|"other"}}]}}

Pages:
{blob}
"""
    try:
        resp = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(resp.text or "{}")
        facts = data.get("facts") if isinstance(data, dict) else None
        if isinstance(facts, list):
            from identity_filter import text_declares_other_linkedin, url_conflicts_with_canonical

            canonical = identity.get("linkedin_url")
            kept = []
            for f in facts:
                if not isinstance(f, dict):
                    continue
                src = f.get("source_url") or ""
                fact = f.get("fact") or ""
                if url_conflicts_with_canonical(src, canonical) or text_declares_other_linkedin(
                    f"{src}\n{fact}", canonical
                ):
                    print(f"  [deep_agent] drop fact from other identity: {src}", flush=True)
                    continue
                kept.append(f)
            return kept[:20]
    except (errors.ClientError, errors.ServerError, json.JSONDecodeError, TypeError) as exc:
        print(f"  [deep_agent] extract error: {exc}", flush=True)
    return []


def _critique_gaps(client, *, identity: dict, known: dict, evidence: list) -> dict:
    prompt = f"""Identity: {json.dumps(identity)}
Evidence count: {len(evidence)}
Sample: {json.dumps(evidence[:8], default=str)[:2000]}

For a meeting briefing, do we need another public-web search hop?
Return JSON:
{{"needs_more": bool, "summary": string, "next_queries": [string]}}
Prefer needs_more=false if we already have career + at least a few cited facts.
"""
    try:
        resp = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(resp.text or "{}")
        if isinstance(data, dict):
            return data
    except Exception as exc:
        print(f"  [deep_agent] critique error: {exc}", flush=True)
    return {"needs_more": False, "summary": "critique failed", "next_queries": []}


def _discover_socials_once(
    *,
    name: str,
    company: Optional[str],
    linkedin_url: Optional[str],
) -> dict:
    """One attempt per platform for public profile URLs."""
    from connectors.social_find import find_profile_link

    links: dict[str, Optional[str]] = {}
    details: dict[str, Any] = {}
    found = False
    for platform in ("instagram", "twitter", "facebook"):
        try:
            hit = find_profile_link(name, platform, company=company)
            details[platform] = {
                "status": hit.get("status"),
                "url": hit.get("url"),
                "handle": hit.get("handle"),
                "attempts": len(hit.get("attempts") or []),
            }
            if hit.get("status") == "ok" and hit.get("url"):
                links[platform] = hit["url"]
                found = True
        except Exception as exc:
            details[platform] = {"status": "error", "error": str(exc)[:200]}
    if linkedin_url:
        links["linkedin"] = linkedin_url
    return {"found": found, "links": links, "details": details}


def _dedupe_urls(urls: list[str]) -> list[str]:
    out = []
    seen = set()
    for u in urls:
        try:
            p = urlparse(u)
            key = f"{p.netloc.lower()}{p.path.rstrip('/').lower()}"
        except Exception:
            key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out
