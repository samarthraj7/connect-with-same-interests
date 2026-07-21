"""Social profile URL discovery.

Strategy:
1. Up to 4 different Google searches for the platform profile URL
2. Prefer the first real profile link (resolve Google grounding redirects)
3. If Google still fails, ScrapeCreators name-only search (Instagram) —
   query is JUST the person's name, never company/extra junk
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
    """Find a profile URL/handle: 4 Google attempts, then ScrapeCreators name-only."""
    platform = platform.lower().strip()
    if platform not in PLATFORM_HOSTS:
        return {"status": "error", "error": f"unsupported platform: {platform}"}

    attempts: List[dict] = []

    if known_url:
        resolved = _resolve_url(known_url)
        if _is_profile_url(resolved, platform):
            handle = _handle_from_url(resolved, platform)
            return {
                "status": "ok",
                "url": resolved,
                "handle": handle,
                "method": "known_url",
                "query": None,
                "attempts": attempts,
                "grounding_urls": [],
            }

    for i, query in enumerate(_google_queries(name, platform, company), start=1):
        print(f"  [social] Google attempt {i}/{MAX_GOOGLE_ATTEMPTS}: {query!r}")
        hit = _google_first_profile(query, platform, name)
        ok = isinstance(hit, dict) and hit.get("status") == "ok"
        attempt_rec = {
            "attempt": i,
            "query": query,
            "success": ok,
            "grounding_urls": (hit or {}).get("grounding_urls") or [],
            "raw_grounding": (hit or {}).get("raw_grounding") or [],
        }
        attempts.append(attempt_rec)
        if ok:
            hit["attempts"] = attempts
            print(f"  [social] caught URL on attempt {i}: {hit.get('url')}")
            return hit

    # Fallback: ScrapeCreators with JUST the name (no company, no operators)
    print(f"  [social] Google failed after {MAX_GOOGLE_ATTEMPTS} tries — ScrapeCreators name-only: {name!r}")
    sc_hit = _scrapecreators_name_search(name, platform)
    if sc_hit:
        sc_hit["attempts"] = attempts
        sc_hit["method"] = "scrapecreators_name_only"
        print(f"  [social] ScrapeCreators name hit -> {sc_hit.get('url')} (@{sc_hit.get('handle')})")
        return sc_hit

    return {
        "status": "not_found",
        "url": None,
        "handle": None,
        "method": "exhausted",
        "query": None,
        "attempts": attempts,
        "grounding_urls": [],
        "reason": f"no {platform} profile URL after {MAX_GOOGLE_ATTEMPTS} Google attempts + ScrapeCreators name search",
    }


def _google_queries(name: str, platform: str, company: Optional[str]) -> List[str]:
    label = {"instagram": "Instagram", "facebook": "Facebook", "twitter": "Twitter"}[platform]
    host = {"instagram": "instagram.com", "facebook": "facebook.com", "twitter": "twitter.com"}[platform]
    queries = [
        f'"{name}" {label}',
        f"{name} {label}",
        f'site:{host} "{name}"',
        f'"{name}" {label} profile',
    ]
    # If company exists, swap attempt 4 for a name+company variant (still short)
    if company:
        queries[3] = f'"{name}" {label} {company.split(",")[0].strip()}'
    return queries[:MAX_GOOGLE_ATTEMPTS]


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
