"""Nimble Extract — pull clean markdown/HTML from URLs found during research.

Docs: https://docs.nimbleway.com/api-reference/extract/extract
Auth: Authorization: Bearer <NIMBLE_API_KEY>
Endpoint: POST https://sdk.nimbleway.com/v1/extract

Env:
  NIMBLE_API_KEY          required to enable
  NIMBLE_EXTRACT_URL      override base (default sdk.nimbleway.com/v1/extract)
  NIMBLE_RENDER           true|false|auto (default auto)
  NIMBLE_DRIVER           optional vx6|vx8|… (omit to let Nimble choose)
  NIMBLE_MAX_PAGES        max URLs per research run (default 8)
  NIMBLE_TIMEOUT          request timeout seconds (default 60)
  NIMBLE_MAX_CHARS        truncate markdown per page (default 12000)
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable, List, Optional
from urllib.parse import urlparse

import requests

EXTRACT_URL = (
    os.environ.get("NIMBLE_EXTRACT_URL") or "https://sdk.nimbleway.com/v1/extract"
).rstrip("/")
TIMEOUT = float(os.environ.get("NIMBLE_TIMEOUT") or "60")
MAX_PAGES = int(os.environ.get("NIMBLE_MAX_PAGES") or "8")
MAX_CHARS = int(os.environ.get("NIMBLE_MAX_CHARS") or "12000")
MAX_WORKERS = int(os.environ.get("NIMBLE_MAX_WORKERS") or "4")

# Skip social shells / Google grounding wrappers — low signal for person dossiers
_SKIP_HOST_SUBSTR = (
    "instagram.com",
    "facebook.com",
    "fb.com",
    "fb.me",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "vertexaisearch.cloud.google.com",
    "google.com/search",
    "bing.com/search",
)


def configured() -> bool:
    return bool((os.environ.get("NIMBLE_API_KEY") or "").strip())


def extract_url(
    url: str,
    *,
    render: Optional[Any] = None,
    formats: Optional[List[str]] = None,
    driver: Optional[str] = None,
) -> dict[str, Any]:
    """Extract one page via Nimble. Returns normalized {status, url, markdown, …}."""
    api_key = (os.environ.get("NIMBLE_API_KEY") or "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "NIMBLE_API_KEY not set", "url": url}
    if not url or not str(url).startswith("http"):
        return {"status": "error", "error": "invalid url", "url": url}
    if _should_skip_url(url):
        return {"status": "skipped", "reason": "url_skipped_host", "url": url}

    formats = formats or ["markdown"]
    if render is None:
        render = _render_setting()
    body: dict[str, Any] = {
        "url": url,
        "formats": formats,
        "render": render,
    }
    drv = driver or (os.environ.get("NIMBLE_DRIVER") or "").strip()
    if drv:
        body["driver"] = drv

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(EXTRACT_URL, headers=headers, json=body, timeout=TIMEOUT)
        if resp.status_code == 402:
            return {
                "status": "error",
                "error": "Nimble payment required / credits exhausted",
                "url": url,
                "http_status": 402,
            }
        if resp.status_code == 429:
            return {
                "status": "error",
                "error": "Nimble rate limited",
                "url": url,
                "http_status": 429,
            }
        if resp.status_code >= 400:
            return {
                "status": "error",
                "error": f"Nimble HTTP {resp.status_code}: {(resp.text or '')[:240]}",
                "url": url,
                "http_status": resp.status_code,
            }
        payload = resp.json() if resp.content else {}
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)[:300], "url": url}
    except ValueError as exc:
        return {"status": "error", "error": f"invalid JSON: {exc}", "url": url}

    return _normalize_response(url, payload)


def extract_many(
    urls: Iterable[str],
    *,
    max_pages: Optional[int] = None,
    render: Optional[Any] = None,
) -> dict[str, Any]:
    """Extract several URLs in parallel. Primary research entrypoint."""
    if not configured():
        return {"status": "skipped", "reason": "NIMBLE_API_KEY not set", "pages": []}

    limit = max(1, min(int(max_pages or MAX_PAGES), 20))
    cleaned: List[str] = []
    seen = set()
    for u in urls:
        u = (u or "").strip()
        if not u or not u.startswith("http"):
            continue
        key = u.split("#", 1)[0].rstrip("/").lower()
        if key in seen or _should_skip_url(u):
            continue
        seen.add(key)
        cleaned.append(u)
        if len(cleaned) >= limit:
            break

    if not cleaned:
        return {"status": "not_found", "pages": [], "reason": "no extractable urls"}

    print(f"  [nimble] extracting {len(cleaned)} page(s)…", flush=True)
    pages: List[dict] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(cleaned))) as pool:
        futs = {pool.submit(extract_url, u, render=render): u for u in cleaned}
        for fut in as_completed(futs):
            row = fut.result()
            if row.get("status") == "ok":
                pages.append(row)
                print(
                    f"  [nimble] ok {row.get('final_url') or row.get('url')!r} "
                    f"chars={len(row.get('markdown') or row.get('text') or '')}",
                    flush=True,
                )
            else:
                errors += 1
                print(
                    f"  [nimble] {row.get('status')} {futs[fut]!r}: "
                    f"{row.get('error') or row.get('reason')}",
                    flush=True,
                )

    if not pages:
        return {
            "status": "error" if errors else "not_found",
            "pages": [],
            "attempted": len(cleaned),
            "errors": errors,
        }
    return {
        "status": "ok",
        "pages": pages,
        "attempted": len(cleaned),
        "ok_count": len(pages),
        "errors": errors,
    }


def collect_urls_from_sources(sources: dict, *, limit: Optional[int] = None) -> List[str]:
    """Pull candidate page URLs from Gemini/Exa/public_web/deep_agent results."""
    limit = max(1, min(int(limit or MAX_PAGES * 2), 40))
    out: List[str] = []
    seen = set()

    def add(u: Optional[str]):
        if len(out) >= limit:
            return
        u = (u or "").strip()
        if not u.startswith("http"):
            return
        key = u.split("#", 1)[0].rstrip("/").lower()
        if key in seen or _should_skip_url(u):
            return
        seen.add(key)
        out.append(u)

    gem = sources.get("gemini_search") or {}
    if isinstance(gem, dict):
        for s in gem.get("sources") or []:
            if isinstance(s, dict):
                add(s.get("url"))
            elif isinstance(s, (list, tuple)) and s:
                add(s[0] if isinstance(s[0], str) else None)
        for pg in gem.get("verified_pages") or []:
            if isinstance(pg, dict):
                add(pg.get("url") or pg.get("requested_url"))
        links = gem.get("social_profile_links") or {}
        if isinstance(links, dict):
            for k, v in links.items():
                if k == "linkedin":
                    continue  # login-gated; Enrich Layer / linkedin_public own this
                add(v)

    exa = sources.get("exa_search") or {}
    if isinstance(exa, dict):
        for m in exa.get("mentions") or []:
            if isinstance(m, dict):
                add(m.get("url"))
        for r in exa.get("general_results") or []:
            if isinstance(r, dict):
                add(r.get("url"))

    pw = sources.get("public_web") or {}
    if isinstance(pw, dict):
        for bucket in ("portfolios", "writing_and_talks", "other_public_pages"):
            for h in pw.get(bucket) or []:
                if isinstance(h, dict):
                    add(h.get("url"))

    deep = sources.get("deep_agent") or {}
    if isinstance(deep, dict):
        for e in deep.get("evidence") or []:
            if isinstance(e, dict):
                add(e.get("source_url") or e.get("url"))
        for u in deep.get("urls") or []:
            add(u if isinstance(u, str) else None)

    pi = sources.get("personal_info") or {}
    if isinstance(pi, dict):
        for ev in pi.get("evidence") or []:
            if isinstance(ev, dict):
                add(ev.get("url") or ev.get("source_url"))

    return out


def pages_as_text_blobs(nimble_result: dict, *, max_chars: Optional[int] = None) -> List[dict]:
    """Shape for deep_agent / synthesize: [{url, text}]."""
    cap = int(max_chars or MAX_CHARS)
    out = []
    for p in (nimble_result or {}).get("pages") or []:
        if p.get("status") != "ok":
            continue
        text = (p.get("markdown") or p.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "url": p.get("final_url") or p.get("url"),
                "text": text[:cap],
                "title": p.get("title"),
                "extractor": "nimble",
            }
        )
    return out


def enrich_person_pages(
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    seed_sources: Optional[dict] = None,
) -> dict[str, Any]:
    """Directed extract: university + company pages about this person, then 1-hop follow.

    Flow:
      1) Prefer search hits that look like school directories / employer bios
      2) Actively discover more via Exa (name + university / company)
      3) Nimble-extract those pages
      4) From markdown, pull same-site bio/team/profile links → Nimble again
    """
    if not configured():
        return {"status": "skipped", "reason": "NIMBLE_API_KEY not set", "pages": []}

    name = (name or "").strip()
    company = (company or "").strip() or None
    university = (university or "").strip() or None
    if not name:
        return {"status": "error", "error": "name required", "pages": []}

    hop1_budget = max(3, min(MAX_PAGES, 10))
    hop2_budget = int(os.environ.get("NIMBLE_FOLLOW_LINKS") or "4")

    # --- gather seed URLs (general + org-flavored) ---
    seeds = collect_urls_from_sources(seed_sources or {}, limit=hop1_budget * 2)
    orgish = [
        u
        for u in seeds
        if _url_looks_org_page(u, company=company, university=university)
    ]
    discovered = _discover_org_urls(
        name=name,
        company=company,
        university=university,
        limit=hop1_budget,
    )

    hop1_urls: List[str] = []
    seen: set = set()

    def push(u: Optional[str], *, prefer: bool = False):
        u = (u or "").strip()
        if not u.startswith("http") or _should_skip_url(u):
            return
        key = u.split("#", 1)[0].rstrip("/").lower()
        if key in seen:
            return
        if len(hop1_urls) >= hop1_budget and not prefer:
            return
        if prefer and len(hop1_urls) >= hop1_budget:
            # still allow preferred org pages by replacing capacity conceptually — just append if under soft cap
            if len(hop1_urls) >= hop1_budget + 2:
                return
        seen.add(key)
        hop1_urls.append(u)

    for u in orgish:
        push(u, prefer=True)
    for u in discovered:
        push(u, prefer=True)
    for u in seeds:
        push(u)

    print(
        f"  [nimble] directed hop1 name={name!r} company={company!r} "
        f"university={university!r} urls={len(hop1_urls)} "
        f"(orgish={len(orgish)} discovered={len(discovered)})",
        flush=True,
    )
    hop1 = extract_many(hop1_urls, max_pages=len(hop1_urls) or 1)
    pages = list(hop1.get("pages") or [])

    # --- hop 2: follow promising links from extracted org pages ---
    follow: List[str] = []
    if hop2_budget > 0 and pages:
        for p in pages:
            md = p.get("markdown") or p.get("text") or ""
            base = p.get("final_url") or p.get("url") or ""
            for link in _person_links_from_markdown(
                md,
                base_url=base,
                name=name,
                company=company,
                university=university,
            ):
                key = link.split("#", 1)[0].rstrip("/").lower()
                if key in seen or _should_skip_url(link):
                    continue
                seen.add(key)
                follow.append(link)
                if len(follow) >= hop2_budget:
                    break
            if len(follow) >= hop2_budget:
                break

    hop2_pages: List[dict] = []
    if follow:
        print(f"  [nimble] directed hop2 follow links={len(follow)}", flush=True)
        hop2 = extract_many(follow, max_pages=len(follow))
        hop2_pages = list(hop2.get("pages") or [])
        for p in hop2_pages:
            p["hop"] = 2
        for p in pages:
            p.setdefault("hop", 1)

    all_pages = pages + hop2_pages
    if not all_pages:
        return {
            "status": hop1.get("status") or "not_found",
            "pages": [],
            "hop1_urls": hop1_urls,
            "hop2_urls": follow,
            "reason": hop1.get("reason") or "no pages extracted",
            "mode": "directed_org",
        }

    return {
        "status": "ok",
        "pages": all_pages,
        "ok_count": len(all_pages),
        "hop1_urls": hop1_urls,
        "hop2_urls": follow,
        "hop1_ok": len(pages),
        "hop2_ok": len(hop2_pages),
        "mode": "directed_org",
        "company": company,
        "university": university,
    }


def _discover_org_urls(
    *,
    name: str,
    company: Optional[str],
    university: Optional[str],
    limit: int = 8,
) -> List[str]:
    """Exa search aimed at school directories and employer people/bio pages."""
    api_key = (os.environ.get("EXA_API_KEY") or "").strip()
    if not api_key:
        return []

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    queries: List[str] = []
    if university:
        queries.extend(
            [
                f'"{name}" "{university}" (directory OR alumni OR faculty OR student OR profile)',
                f'"{name}" "{university}" site:.edu',
            ]
        )
        for alias in _light_aliases(university)[:2]:
            if alias.lower() != university.lower():
                queries.append(f'"{name}" "{alias}" (alumni OR directory OR student)')
    if company:
        queries.extend(
            [
                f'"{name}" "{company}" (team OR people OR leadership OR about OR bio OR staff)',
                f'"{name}" "{company}" (engineer OR founder OR director OR "works at" OR employee)',
            ]
        )
        for alias in _light_aliases(company)[:2]:
            if alias.lower() != company.lower():
                queries.append(f'"{name}" "{alias}" (team OR bio OR people)')

    if not queries:
        return []

    urls: List[str] = []
    seen: set = set()
    for q in queries[:6]:
        if len(urls) >= limit:
            break
        try:
            from connectors.exa_search import _run_search

            hits = _run_search(headers, q, num_results=5, want_text=False) or []
        except Exception as exc:
            print(f"  [nimble] org discover search fail: {exc}", flush=True)
            continue
        for h in hits:
            u = (h.get("url") or "").strip()
            if not u.startswith("http") or _should_skip_url(u):
                continue
            # Skip pure LinkedIn — handled elsewhere
            if "linkedin.com" in u.lower():
                continue
            key = u.split("#", 1)[0].rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            urls.append(u)
            print(f"  [nimble] discovered org page: {u[:100]}", flush=True)
            if len(urls) >= limit:
                break
    return urls


def _url_looks_org_page(
    url: str,
    *,
    company: Optional[str],
    university: Optional[str],
) -> bool:
    u = (url or "").lower()
    path_hints = (
        "/people",
        "/team",
        "/about",
        "/bio",
        "/staff",
        "/faculty",
        "/alumni",
        "/directory",
        "/profile",
        "/students",
        "/leadership",
        "/authors",
        "/speakers",
    )
    if any(h in u for h in path_hints):
        return True
    if ".edu" in u or "ac.uk" in u:
        return True
    blob = u
    for org in (company, university):
        if not org:
            continue
        tokens = [t for t in re.split(r"[^a-z0-9]+", org.lower()) if len(t) > 2]
        if tokens and sum(1 for t in tokens[:3] if t in blob) >= min(2, len(tokens[:3])):
            return True
    return False


def _person_links_from_markdown(
    markdown: str,
    *,
    base_url: str,
    name: str,
    company: Optional[str],
    university: Optional[str],
) -> List[str]:
    """Pull follow-up URLs from an extracted page (same site / bio-ish paths)."""
    if not markdown:
        return []
    base_host = urlparse(base_url).netloc.lower().lstrip("www.") if base_url else ""
    name_tokens = [t.lower() for t in re.split(r"[^A-Za-z]+", name or "") if len(t) > 1]
    links = re.findall(r"https?://[^\s\)\]\>\"']+", markdown)
    # markdown [text](url)
    links += re.findall(r"\[[^\]]*\]\((https?://[^)\s]+)\)", markdown)

    scored: List[tuple] = []
    seen = set()
    for raw in links:
        u = raw.rstrip(".,;:)")
        if not u.startswith("http") or _should_skip_url(u):
            continue
        if "linkedin.com" in u.lower():
            continue
        key = u.split("#", 1)[0].rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        host = urlparse(u).netloc.lower().lstrip("www.")
        path = (urlparse(u).path or "").lower()
        score = 0
        if base_host and (host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)):
            score += 3
        if any(h in path for h in ("/people", "/team", "/bio", "/profile", "/faculty", "/alumni", "/directory", "/staff")):
            score += 3
        # Link text proximity isn't available; use slug containing name tokens
        slug = path.replace("-", " ").replace("_", " ").replace("/", " ")
        if name_tokens and all(t in slug or t in u.lower() for t in name_tokens[:2]):
            score += 4
        elif name_tokens and name_tokens[0] in slug:
            score += 2
        if company and any(t in u.lower() for t in re.split(r"[^a-z0-9]+", company.lower()) if len(t) > 3):
            score += 1
        if university and (".edu" in host or any(t in u.lower() for t in re.split(r"[^a-z0-9]+", university.lower()) if len(t) > 3)):
            score += 1
        if score >= 3:
            scored.append((score, u))

    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:8]]


def _light_aliases(org: str) -> List[str]:
    try:
        from connectors.exa_search import _org_aliases

        return _org_aliases(org) or [org]
    except Exception:
        return [org]


def _normalize_response(requested_url: str, payload: dict) -> dict[str, Any]:
    status = (payload.get("status") or "").lower()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    # Some responses put markdown at top level
    markdown = (
        data.get("markdown")
        or payload.get("markdown")
        or ""
    )
    html = data.get("html") or payload.get("html") or ""
    text = markdown or _html_to_rough_text(html)
    text = (text or "").strip()
    if status in ("failed", "error") or (not text and status != "success"):
        err = payload.get("message") or payload.get("error") or "empty extract"
        if not text:
            return {
                "status": "error",
                "error": str(err)[:300],
                "url": requested_url,
                "raw_status": status or None,
            }

    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    rp = meta.get("response_parameters") if isinstance(meta.get("response_parameters"), dict) else {}
    final_url = rp.get("input_url") or data.get("url") or requested_url
    title = _title_from_markdown(markdown) if markdown else None

    return {
        "status": "ok",
        "url": requested_url,
        "final_url": final_url,
        "title": title,
        "markdown": (markdown or "")[:MAX_CHARS] if markdown else None,
        "text": text[:MAX_CHARS],
        "task_id": payload.get("task_id"),
        "driver": meta.get("driver") or data.get("driver"),
        "http_status_code": payload.get("status_code") or data.get("status_code"),
        "extractor": "nimble",
    }


def _render_setting() -> Any:
    raw = (os.environ.get("NIMBLE_RENDER") or "auto").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    if raw == "auto":
        return "auto"
    return "auto"


def _should_skip_url(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in _SKIP_HOST_SUBSTR)


def _title_from_markdown(md: str) -> Optional[str]:
    for line in (md or "").splitlines()[:12]:
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip()[:200] or None
    return None


def _html_to_rough_text(html: str) -> str:
    if not html:
        return ""
    raw = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()
