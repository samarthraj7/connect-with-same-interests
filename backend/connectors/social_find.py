"""Social profile URL discovery.

Strategy:
1. Google Search for platform profile URLs (name / name+company)
2. Synthetic username guesses from the person's name
3. Name-filter candidates (title/handle must match name tokens)
4. ScrapeCreators is NOT used for name-search discovery — callers deep-fetch
   only after name (+ photo) verification (see instagram.py).
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests
from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
SCRAPECREATORS_BASE = "https://api.scrapecreators.com"
TIMEOUT = 20
REDIRECT_TIMEOUT = 8
# One Google attempt per platform (Deepsearch-style speed; avoid 3–4 retries)
MAX_GOOGLE_ATTEMPTS = int(os.environ.get("SOCIAL_FIND_MAX_ATTEMPTS") or "1")

PLATFORM_HOSTS = {
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "twitter": ("twitter.com", "x.com"),
}

_IG_SKIP = {
    "p", "reel", "reels", "stories", "explore", "accounts", "direct",
    "about", "legal", "developer", "directory", "nametag",
}
_FB_SKIP = {
    "public", "watch", "events", "groups", "pages", "marketplace",
    "gaming", "login", "help", "privacy", "policies", "sharer",
}
_TW_SKIP = {
    "i", "intent", "share", "search", "home", "explore", "settings",
    "messages", "notifications", "compose", "login", "signup",
}


def find_profile_link(
    name: str,
    platform: str,
    company: Optional[str] = None,
    known_url: Optional[str] = None,
) -> dict:
    """Find a profile URL/handle: Google + username guesses (no SC name search)."""
    multi = find_profile_candidates(name, platform, company=company, known_url=known_url, max_candidates=1)
    cands = multi.get("candidates") or []
    if cands:
        top = cands[0]
        return {
            "status": "ok",
            "url": top.get("url"),
            "handle": top.get("handle"),
            "method": top.get("method") or multi.get("method"),
            "query": multi.get("query"),
            "attempts": multi.get("attempts") or [],
            "grounding_urls": multi.get("grounding_urls") or [],
            "candidates": cands,
        }
    return {
        "status": "not_found",
        "url": None,
        "handle": None,
        "method": multi.get("method") or "exhausted",
        "query": None,
        "attempts": multi.get("attempts") or [],
        "grounding_urls": multi.get("grounding_urls") or [],
        "reason": multi.get("reason")
        or f"no {platform} profile URL after Google + username guesses",
        "candidates": [],
    }


def find_profile_candidates(
    name: str,
    platform: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    distinguishable_factor: Optional[str] = None,
    known_url: Optional[str] = None,
    max_candidates: int = 10,
    search_constraints: Optional[dict] = None,
) -> dict:
    """Collect profile candidates from Google + username guesses; name-filter them.

    Does NOT call ScrapeCreators name search — deep fetch happens later for
    verified handles only.
    """
    platform = platform.lower().strip()
    if platform not in PLATFORM_HOSTS:
        return {"status": "error", "error": f"unsupported platform: {platform}", "candidates": []}

    max_candidates = max(1, min(int(max_candidates or 10), 12))
    attempts: List[dict] = []
    seen: set = set()
    candidates: List[dict] = []
    sc = search_constraints or {}
    reject_handles = {h.lower().lstrip("@") for h in (sc.get("reject_handles") or []) if h}
    hint = (distinguishable_factor or sc.get("distinguishable_factor") or "").strip() or None
    uni = (university or "").strip() or None

    def _add(url: Optional[str], *, method: str, title: Optional[str] = None, handle: Optional[str] = None):
        if len(candidates) >= max_candidates * 2:  # gather extra before name filter
            return
        resolved = _resolve_url(url) if url else None
        h = (handle or _handle_from_url(resolved or "", platform) or "").lstrip("@").strip()
        if not h and resolved and _is_profile_url(resolved, platform):
            h = _handle_from_url(resolved, platform) or ""
        if not h:
            return
        key = h.lower()
        if key in seen or key in reject_handles:
            return
        if resolved and not _is_profile_url(resolved, platform):
            resolved = _url_for_handle(h, platform)
        if not resolved:
            resolved = _url_for_handle(h, platform)
        seen.add(key)
        candidates.append(
            {
                "handle": h,
                "url": resolved,
                "method": method,
                "title": title,
            }
        )

    if known_url:
        resolved = _resolve_url(known_url)
        if _is_profile_url(resolved, platform):
            _add(resolved, method="known_url")

    queries = _google_queries(
        name,
        platform,
        company,
        university=uni,
        distinguishable_factor=hint,
    )
    max_attempts = max(MAX_GOOGLE_ATTEMPTS, 3 if (company or uni or hint) else MAX_GOOGLE_ATTEMPTS)
    for i, query in enumerate(queries[:max_attempts], start=1):
        if len(candidates) >= max_candidates * 2:
            break
        print(f"  [social] Google candidates attempt {i}/{max_attempts}: {query!r}")
        hit = _google_collect_profiles(query, platform, name, limit=max_candidates)
        ok = bool((hit or {}).get("candidates"))
        attempts.append(
            {
                "attempt": i,
                "query": query,
                "success": ok,
                "count": len((hit or {}).get("candidates") or []),
                "grounding_urls": (hit or {}).get("grounding_urls") or [],
            }
        )
        for c in (hit or {}).get("candidates") or []:
            _add(c.get("url"), method=c.get("method") or "google", title=c.get("title"), handle=c.get("handle"))

    # Username guesses from name (no API) — later verified by name + face before SC deep-fetch
    for guess in _username_guesses(name):
        _add(None, method="username_guess", handle=guess, title=name)

    # Name-filter: keep candidates whose title/handle plausibly match the person
    filtered = [c for c in candidates if _name_matches_candidate(name, c)]
    if not filtered and candidates:
        # Keep known_url + username_guess even if title missing
        filtered = [
            c for c in candidates
            if (c.get("method") or "") in ("known_url", "username_guess")
        ] or candidates[:max_candidates]

    if not filtered:
        return {
            "status": "not_found",
            "candidates": [],
            "method": "exhausted",
            "attempts": attempts,
            "grounding_urls": [],
            "reason": f"no {platform} candidates after Google + name filter (SC deferred)",
        }

    print(
        f"  [social] {platform} candidates={len(filtered[:max_candidates])} "
        f"(pre-SC name-filtered): {[c['handle'] for c in filtered[:max_candidates]]}",
        flush=True,
    )
    return {
        "status": "ok",
        "candidates": filtered[:max_candidates],
        "method": "multi_candidate",
        "attempts": attempts,
        "grounding_urls": (attempts[0].get("grounding_urls") if attempts else []) or [],
        "query": (attempts[0].get("query") if attempts else None),
    }


def _username_guesses(name: str) -> List[str]:
    from handle_variants import username_variants

    return username_variants(name, limit=8)


def _name_matches_candidate(name: str, candidate: dict) -> bool:
    """True if title or handle shares meaningful name tokens with the person."""
    tokens = [t.lower() for t in re.split(r"[^A-Za-z]+", name or "") if len(t) > 1]
    if not tokens:
        return True
    title = (candidate.get("title") or "").lower()
    handle = (candidate.get("handle") or "").lower().replace(".", "").replace("_", "")
    method = candidate.get("method") or ""
    if method == "known_url":
        return True
    if title:
        if all(t in title for t in tokens[:2]):
            return True
        if tokens[0] in title and (len(tokens) < 2 or tokens[-1] in title):
            return True
    # Handle contains first+last initials/parts
    if tokens[0][:3] in handle and (len(tokens) < 2 or tokens[-1][:3] in handle):
        return True
    if method == "username_guess":
        return True  # already derived from name
    return False


def _google_collect_profiles(query: str, platform: str, name: str, *, limit: int = 10) -> Optional[dict]:
    """Google Search → many profile URLs (grounding + model JSON list)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Use Google Search with this query: {query}\n\n"
        f"List up to {limit} distinct {platform} PROFILE page URLs for people who might be named {name}. "
        "Prefer personal profiles, not posts/reels/explore.\n"
        "Return ONLY strict JSON, no markdown:\n"
        '{"profiles":[{"profile_url": string, "handle": string or null, "title": string or null}]}\n'
        "Rules:\n"
        "- profile_url must be https on "
        f"{', '.join(PLATFORM_HOSTS[platform])}.\n"
        "- Do NOT invent handles. Empty list is OK if nothing matches."
    )
    try:
        client = genai.Client(api_key=api_key)
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
    except (errors.ClientError, errors.ServerError) as exc:
        print(f"  [social] Google multi call failed: {exc}")
        return None

    raw_grounding = _grounding_urls(response)
    resolved_grounding: List[Tuple[str, str]] = []
    for url, title in raw_grounding:
        final = _resolve_url(url)
        resolved_grounding.append((final, title))

    collected: List[dict] = []
    seen: set = set()

    def push(url: str, method: str, title: Optional[str] = None, handle: Optional[str] = None):
        if len(collected) >= limit:
            return
        if not _is_profile_url(url, platform):
            return
        h = (handle or _handle_from_url(url, platform) or "").lower()
        if not h or h in seen:
            return
        seen.add(h)
        collected.append(
            {
                "url": url,
                "handle": _handle_from_url(url, platform),
                "title": title,
                "method": method,
            }
        )

    for url, title in resolved_grounding:
        push(url, "google_grounding", title)
        h = _handle_from_title(title or "", platform)
        if h:
            push(_url_for_handle(h, platform), "google_grounding_title", title, h)

    parsed = _parse_json(response.text or "")
    for row in (parsed or {}).get("profiles") or []:
        if not isinstance(row, dict):
            continue
        url = _resolve_url(row.get("profile_url") or "")
        push(url, "google_model_json", row.get("title"), row.get("handle"))

    for match_url in _urls_in_text(response.text or "", platform):
        push(_resolve_url(match_url), "google_text_scan")

    return {
        "candidates": collected,
        "grounding_urls": [{"url": u, "title": t} for u, t in resolved_grounding[:12]],
    }



def _google_queries(
    name: str,
    platform: str,
    company: Optional[str],
    *,
    university: Optional[str] = None,
    distinguishable_factor: Optional[str] = None,
) -> List[str]:
    label = {"instagram": "Instagram", "facebook": "Facebook", "twitter": "Twitter"}[platform]
    host = {"instagram": "instagram.com", "facebook": "facebook.com", "twitter": "twitter.com"}[platform]
    # Prioritize disambiguating queries first — common names need the hint/org early
    queries: List[str] = []
    if company:
        co = company.split(",")[0].strip()
        queries.append(f'"{name}" {label} {co}')
        queries.append(f'"{name}" {co} site:{host}')
    if university:
        uni = university.split(",")[0].strip()
        queries.append(f'"{name}" {label} {uni}')
        queries.append(f'"{name}" {uni} site:{host}')
    if distinguishable_factor:
        hint = distinguishable_factor.strip()
        # Take first few tokens/phrases
        bits = [b.strip() for b in re.split(r"[,;/|]+", hint) if b.strip()][:3] or [hint]
        for bit in bits:
            queries.append(f'"{name}" {label} {bit}')
            queries.append(f'"{name}" {bit} site:{host}')
    queries.extend(
        [
            f'"{name}" {label}',
            f"{name} {label}",
            f'site:{host} "{name}"',
            f'"{name}" {label} profile',
        ]
    )
    # De-dupe preserve order
    seen = set()
    out = []
    for q in queries:
        k = q.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(q)
    return out


def _google_first_profile(query: str, platform: str, name: str) -> Optional[dict]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Use Google Search with this query: {query}\n\n"
        f"Your only job: return the top {platform} PROFILE page URL for the person named {name}. "
        "The first organic profile result is usually correct.\n"
        "Return ONLY strict JSON, no markdown:\n"
        '{"profile_url": string or null, "handle": string or null, "title": string or null}\n'
        "Rules:\n"
        "- profile_url must be a real https URL on the platform domain "
        f"({', '.join(PLATFORM_HOSTS[platform])}).\n"
        "- Do NOT invent a handle. If you only see a redirect, still return the final profile URL if known.\n"
        "- Empty nulls are correct when nothing matching is in the results."
    )
    try:
        client = genai.Client(api_key=api_key)
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
    except (errors.ClientError, errors.ServerError) as exc:
        print(f"  [social] Google call failed: {exc}")
        return None

    raw_grounding = _grounding_urls(response)
    # Resolve Google/Vertex redirect wrappers to real destination URLs
    resolved_grounding: List[Tuple[str, str]] = []
    for url, title in raw_grounding:
        final = _resolve_url(url)
        resolved_grounding.append((final, title))
        print(f"  [social] grounding: {url[:80]}{'...' if len(url) > 80 else ''} -> {final}")

    for url, title in resolved_grounding:
        if _is_profile_url(url, platform):
            return _hit(url, platform, query, resolved_grounding, raw_grounding, "google_grounding_first", title)

    # Also try handles embedded in grounding titles (e.g. "Name (@handle) • Instagram")
    for _, title in resolved_grounding:
        handle = _handle_from_title(title or "", platform)
        if handle:
            url = _url_for_handle(handle, platform)
            return _hit(url, platform, query, resolved_grounding, raw_grounding, "google_grounding_title", title)

    parsed = _parse_json(response.text or "")
    url = _resolve_url((parsed or {}).get("profile_url") or "")
    handle = (parsed or {}).get("handle")
    if url and _is_profile_url(url, platform):
        return _hit(url, platform, query, resolved_grounding, raw_grounding, "google_model_json", (parsed or {}).get("title"))
    if handle and platform in ("instagram", "twitter"):
        cleaned = str(handle).lstrip("@").strip()
        if cleaned:
            return _hit(_url_for_handle(cleaned, platform), platform, query, resolved_grounding, raw_grounding, "google_model_handle")

    for match_url in _urls_in_text(response.text or "", platform):
        final = _resolve_url(match_url)
        if _is_profile_url(final, platform):
            return _hit(final, platform, query, resolved_grounding, raw_grounding, "google_text_scan")

    # Scan raw response for any platform URL strings even inside redirects/unquoted text
    for match_url in _urls_in_text(json.dumps([u for u, _ in raw_grounding]), platform):
        final = _resolve_url(match_url)
        if _is_profile_url(final, platform):
            return _hit(final, platform, query, resolved_grounding, raw_grounding, "google_grounding_embedded")

    print(f"  [social] no profile URL extracted for {query!r}")
    return {
        "status": "miss",
        "grounding_urls": [{"url": u, "title": t} for u, t in resolved_grounding[:8]],
        "raw_grounding": [{"url": u, "title": t} for u, t in raw_grounding[:8]],
    }

def _hit(url, platform, query, resolved, raw, method, title=None) -> dict:
    handle = _handle_from_url(url, platform)
    return {
        "status": "ok",
        "url": url,
        "handle": handle,
        "method": method,
        "query": query,
        "title": title,
        "grounding_urls": [{"url": u, "title": t} for u, t in resolved[:8]],
        "raw_grounding": [{"url": u, "title": t} for u, t in raw[:8]],
    }


def _scrapecreators_name_search(name: str, platform: str) -> Optional[dict]:
    """Last resort: ScrapeCreators search with ONLY the person's name."""
    api_key = os.environ.get("SCRAPECREATORS_API_KEY")
    if not api_key:
        print("  [social] SCRAPECREATORS_API_KEY not set — cannot name-search")
        return None

    # Instagram has an explicit search endpoint. For others, try the same pattern
    # if available; otherwise try treating a compacted name as a handle (weak).
    if platform == "instagram":
        return _sc_instagram_search(api_key, name)
    if platform == "twitter":
        return _sc_twitter_name_guess(api_key, name)
    if platform == "facebook":
        # Facebook endpoint wants a URL; without Google we can't invent one safely
        print("  [social] no ScrapeCreators Facebook name-search endpoint — skip")
        return None
    return None


def _sc_instagram_search(api_key: str, name: str) -> Optional[dict]:
    # IMPORTANT: query is JUST the name — no company, no site:, no extra words
    query = name.strip()
    print(f"  [social] ScrapeCreators /instagram/search/profiles query={query!r}")
    try:
        resp = requests.get(
            f"{SCRAPECREATORS_BASE}/v1/instagram/search/profiles",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            params={"query": query},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            print(f"  [social] ScrapeCreators search HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  [social] ScrapeCreators search error: {exc}")
        return None

    profiles = _extract_sc_profiles(data)
    if not profiles:
        print("  [social] ScrapeCreators search returned 0 profiles")
        return None

    name_l = name.lower()
    ranked = sorted(
        profiles,
        key=lambda p: (
            0 if name_l in (p.get("full_name") or "").lower() else 1,
            0 if not p.get("is_private") else 1,
        ),
    )
    best = ranked[0]
    handle = best.get("username") or best.get("handle")
    if not handle:
        return None
    url = best.get("url") or f"https://www.instagram.com/{handle}/"
    return {
        "status": "ok",
        "url": url,
        "handle": handle,
        "query": query,
        "scrapecreators_candidates": [
            {"username": p.get("username") or p.get("handle"), "full_name": p.get("full_name")}
            for p in ranked[:5]
        ],
        "grounding_urls": [],
    }


def _sc_twitter_name_guess(api_key: str, name: str) -> Optional[dict]:
    """Weak fallback: try compacted name variants as Twitter handles."""
    parts = re.findall(r"[A-Za-z0-9]+", name)
    if not parts:
        return None
    guesses = []
    joined = "".join(parts)
    if 1 <= len(joined) <= 15:
        guesses.append(joined)
    if len(parts) >= 2:
        guesses.append((parts[0] + parts[-1])[:15])
        guesses.append((parts[0][0] + parts[-1])[:15])
    for handle in guesses:
        print(f"  [social] trying Twitter handle guess @{handle}")
        try:
            resp = requests.get(
                f"{SCRAPECREATORS_BASE}/v1/twitter/profile",
                headers={"x-api-key": api_key},
                params={"handle": handle},
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                continue
            data = resp.json()
        except (requests.RequestException, ValueError):
            continue
        raw = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
        if not isinstance(raw, dict):
            continue
        user = raw.get("user") if isinstance(raw.get("user"), dict) else raw
        display = (user.get("name") or user.get("full_name") or "").lower()
        if name.lower().split()[0] in display or name.lower() in display:
            return {
                "status": "ok",
                "url": f"https://x.com/{handle}",
                "handle": handle,
                "query": name,
                "grounding_urls": [],
            }
    return None


def _extract_sc_profiles(data) -> list:
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("profiles", "users", "results", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [p for p in val if isinstance(p, dict)]
        if isinstance(val, dict):
            for nested_key in ("profiles", "users", "results"):
                nested = val.get(nested_key)
                if isinstance(nested, list):
                    return [p for p in nested if isinstance(p, dict)]
    return []


def _resolve_url(url: str) -> str:
    """Follow redirects / unwrap Google grounding redirect URLs to the final destination."""
    if not url or not isinstance(url, str):
        return url or ""
    url = url.strip()
    if not url.startswith("http"):
        return url

    # Sometimes the real URL is embedded as a query param
    for key in ("url", "q", "u", "target"):
        if f"{key}=" in url:
            try:
                from urllib.parse import parse_qs, urlparse as _up

                qs = parse_qs(_up(url).query)
                if key in qs and qs[key]:
                    candidate = unquote(qs[key][0])
                    if candidate.startswith("http") and any(
                        h in candidate for hosts in PLATFORM_HOSTS.values() for h in hosts
                    ):
                        return candidate
            except Exception:
                pass

    # Already a clean platform URL
    host = (urlparse(url).hostname or "").lower()
    if any(h in host for hosts in PLATFORM_HOSTS.values() for h in hosts):
        return url.split("?")[0].rstrip("/") + ("/" if "instagram.com" in host else "")

    # Follow redirects for Vertex/Google grounding wrappers
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "ConnectDeeplyBot/0.1"},
            timeout=REDIRECT_TIMEOUT,
            allow_redirects=True,
        )
        final = str(resp.url or url)
        return final
    except requests.RequestException:
        return url


def _grounding_urls(response) -> List[Tuple[str, str]]:
    urls = []
    try:
        for candidate in response.candidates or []:
            metadata = getattr(candidate, "grounding_metadata", None)
            if not metadata:
                continue
            if metadata.grounding_chunks:
                for chunk in metadata.grounding_chunks:
                    web = getattr(chunk, "web", None)
                    if web and web.uri:
                        urls.append((web.uri, web.title or ""))
            # Some SDK versions expose grounding_supports / search_entry_point with rendered content
            entry = getattr(metadata, "search_entry_point", None)
            if entry is not None:
                rendered = getattr(entry, "rendered_content", None) or ""
                for m in re.findall(r'href="(https?://[^"]+)"', rendered):
                    urls.append((m, ""))
    except AttributeError:
        pass
    return urls


def _parse_json(text: str) -> Optional[dict]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _urls_in_text(text: str, platform: str) -> List[str]:
    hosts = "|".join(re.escape(h) for h in PLATFORM_HOSTS[platform])
    pattern = rf"https?://(?:www\.)?(?:{hosts})/[^\s\"'<>\\]+"
    return re.findall(pattern, text or "", flags=re.IGNORECASE)


def _handle_from_title(title: str, platform: str) -> Optional[str]:
    # "Jane Doe (@janedoe) on Instagram" / "Jane Doe - @janedoe"
    m = re.search(r"@([A-Za-z0-9._]{1,30})", title or "")
    if not m:
        return None
    handle = m.group(1)
    if platform == "instagram" and handle.lower() not in _IG_SKIP:
        return handle
    if platform == "twitter" and handle.lower() not in _TW_SKIP and len(handle) <= 15:
        return handle
    if platform == "facebook" and handle.lower() not in _FB_SKIP:
        return handle
    return None


def _url_for_handle(handle: str, platform: str) -> str:
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "twitter":
        return f"https://x.com/{handle}"
    return f"https://www.facebook.com/{handle}"


def _is_profile_url(url: Optional[str], platform: str) -> bool:
    if not url or not str(url).startswith("http"):
        return False
    host = (urlparse(url).hostname or "").lower()
    if not any(h in host for h in PLATFORM_HOSTS[platform]):
        return False
    return _handle_from_url(url, platform) is not None


def _handle_from_url(url: Optional[str], platform: str) -> Optional[str]:
    if not url:
        return None
    path = (urlparse(url).path or "").strip("/")
    if not path:
        return None
    part = path.split("/")[0]

    if platform == "instagram":
        if part.lower() in _IG_SKIP or not re.match(r"^[A-Za-z0-9._]{1,30}$", part):
            return None
        return part

    if platform == "twitter":
        if part.lower() in _TW_SKIP or not re.match(r"^[A-Za-z0-9_]{1,15}$", part):
            return None
        return part

    if platform == "facebook":
        if part.lower() in _FB_SKIP:
            return None
        if part.lower() == "profile.php":
            return url
        if re.match(r"^[A-Za-z0-9.]+$", part):
            return part
        return None

    return None


def rank_profile_candidates(
    profiles: List[dict],
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    hint: Optional[str] = None,
    linkedin_slug: Optional[str] = None,
    face_by_handle: Optional[dict] = None,
    profile_url_template: Optional[str] = None,
) -> List[dict]:
    """Score same-name / different-handle candidates (shared by IG / FB / Twitter).

    Expects each profile row shaped like::
      {handle, profile_url?, profile: {full_name|name, biography|bio, ...}, fetch_status?}
    """
    from name_match import is_exact_name_match, name_tokens

    face_by_handle = face_by_handle or {}
    q_tokens = name_tokens(name)
    org_bits: List[str] = []
    for raw in (company, university, place, hint):
        if not raw:
            continue
        org_bits.extend(re.findall(r"[A-Za-z][A-Za-z0-9&.-]{2,}", str(raw).lower()))
    org_bits = list(dict.fromkeys(org_bits))[:12]
    slug = re.sub(r"[^a-z0-9]", "", (linkedin_slug or "").lower())

    ranked: List[dict] = []
    for p in profiles:
        if p.get("fetch_status") == "error":
            continue
        handle = (p.get("handle") or "").lstrip("@")
        if not handle:
            continue
        prof = p.get("profile") or {}
        full_name = prof.get("full_name") or prof.get("name") or ""
        bio = " ".join(
            [
                full_name,
                prof.get("biography") or prof.get("bio") or "",
                prof.get("external_url") or prof.get("website") or "",
                handle,
            ]
        ).lower()
        face_row = face_by_handle.get(handle.lower()) or {}
        face_score = float(face_row.get("score") or 0)
        signals: List[str] = []
        score = 0.0

        if face_score:
            score += face_score * 0.55
            if face_row.get("same_person"):
                score += 8
                signals.append("face_same")
            signals.append(f"face:{int(face_score)}")

        if full_name and is_exact_name_match(name, full_name):
            score += 18
            signals.append("exact_name")
        elif full_name and q_tokens:
            hits = sum(1 for t in q_tokens if t in full_name.lower())
            if hits >= min(2, len(q_tokens)):
                score += 10
                signals.append("name_tokens")

        org_hits = [b for b in org_bits if b in bio]
        if org_hits:
            score += min(22, 6 * len(org_hits))
            signals.append("bio:" + ",".join(org_hits[:3]))

        h_norm = re.sub(r"[^a-z0-9]", "", handle.lower())
        if slug and h_norm:
            slug_looks_named = any(t[:3] in slug for t in q_tokens[:2]) if q_tokens else False
            if slug_looks_named and (slug == h_norm or slug in h_norm or h_norm in slug):
                score += 14
                signals.append("linkedin_slug")
            elif slug_looks_named and len(slug) >= 4 and (slug[:4] in h_norm or h_norm[:4] in slug):
                score += 6
                signals.append("slug_partial")

        if q_tokens and q_tokens[0][:3] in h_norm:
            score += 3

        default_url = (
            (profile_url_template or "").format(handle=handle)
            if profile_url_template
            else None
        )
        ranked.append(
            {
                "handle": handle,
                "full_name": full_name,
                "profile_url": p.get("profile_url") or default_url,
                "profile_pic_url": prof.get("profile_pic_url"),
                "identity_score": round(score, 1),
                "face_score": face_score or None,
                "signals": signals,
            }
        )

    ranked.sort(key=lambda r: (-r["identity_score"], -(r.get("face_score") or 0)))
    return ranked


def pick_ranked_profile(
    ranked: List[dict],
    profiles: List[dict],
    *,
    hard_min: float = 40,
    hard_gap: float = 12,
    soft_min: float = 28,
    soft_gap: float = 8,
) -> Optional[dict]:
    """Pick a clear/soft winner from rank_profile_candidates output, or None."""
    if not ranked:
        return None
    best, second = ranked[0], ranked[1] if len(ranked) > 1 else None
    gap = best["identity_score"] - (second["identity_score"] if second else 0)
    if best["identity_score"] >= hard_min and (gap >= hard_gap or len(ranked) == 1):
        chosen_handle = best["handle"].lower()
    elif best["identity_score"] >= soft_min and gap >= soft_gap:
        chosen_handle = best["handle"].lower()
    else:
        return None
    return next(
        (p for p in profiles if (p.get("handle") or "").lstrip("@").lower() == chosen_handle),
        None,
    )
