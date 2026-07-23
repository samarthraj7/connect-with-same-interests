"""MediaCrawler-style Playwright login-session crawl for Instagram (and stubs for FB/X).

Ops: run once to create a session file:
  python -m connectors.browser_social login instagram

Then research jobs reuse BROWSER_SOCIAL_STATE_DIR/instagram.json.
Public profile read only — no mass comment harvest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, List, Optional

# Allow `python -m connectors.browser_social` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from channels import browser_state_dir


def _state_path(platform: str) -> Path:
    plat = "twitter" if platform in ("x", "twitter") else platform
    return browser_state_dir() / f"{plat}.json"


def configured(platform: str = "instagram") -> bool:
    return _state_path(platform).exists()


def login(platform: str = "instagram", *, headless: bool = False) -> dict[str, Any]:
    """Open a browser for manual login; save storage_state."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "status": "error",
            "error": "playwright not installed — pip install playwright && playwright install chromium",
        }
    urls = {
        "instagram": "https://www.instagram.com/accounts/login/",
        "facebook": "https://www.facebook.com/login",
        "twitter": "https://x.com/login",
        "x": "https://x.com/login",
    }
    start = urls.get(platform) or urls["instagram"]
    path = _state_path(platform)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(start, wait_until="domcontentloaded")
        print(
            f"Log into {platform} in the browser window, then press Enter here…",
            flush=True,
        )
        try:
            input()
        except EOFError:
            pass
        context.storage_state(path=str(path))
        browser.close()
    return {"status": "ok", "path": str(path)}


def search_instagram(query: str, *, limit: int = 8) -> dict[str, Any]:
    if not configured("instagram"):
        return {"status": "skipped", "reason": "no instagram browser session"}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "skipped", "reason": "playwright not installed"}

    candidates: List[dict] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(_state_path("instagram")))
            page = context.new_page()
            # Public search UI
            q = query.strip().replace(" ", "%20")
            page.goto(f"https://www.instagram.com/web/search/topsearch/?query={q}", wait_until="domcontentloaded", timeout=30000)
            # Fallback: explore search page
            if "login" in page.url.lower():
                browser.close()
                return {"status": "error", "error": "instagram session expired — re-run login"}
            body = page.content()
            # Try JSON embedded in topsearch
            try:
                page.goto(
                    f"https://www.instagram.com/web/search/topsearch/?context=blended&query={q}",
                    wait_until="networkidle",
                    timeout=30000,
                )
                text = page.inner_text("body")
                data = json.loads(text) if text.strip().startswith("{") else None
            except Exception:
                data = None
            if isinstance(data, dict):
                for u in (data.get("users") or [])[:limit]:
                    user = u.get("user") if isinstance(u, dict) else None
                    if not isinstance(user, dict):
                        continue
                    handle = user.get("username")
                    if not handle:
                        continue
                    candidates.append(
                        {
                            "handle": handle,
                            "url": f"https://www.instagram.com/{handle}/",
                            "full_name": user.get("full_name"),
                            "photo_url": user.get("profile_pic_url"),
                            "method": "browser",
                            "title": user.get("full_name") or handle,
                        }
                    )
            if not candidates:
                # Scrape profile links from search HTML as last resort
                for m in re.finditer(r"instagram\.com/([A-Za-z0-9._]+)/", body):
                    handle = m.group(1)
                    if handle in ("accounts", "web", "explore", "p", "reel", "stories"):
                        continue
                    if any(c["handle"] == handle for c in candidates):
                        continue
                    candidates.append(
                        {
                            "handle": handle,
                            "url": f"https://www.instagram.com/{handle}/",
                            "method": "browser_html",
                            "title": handle,
                        }
                    )
                    if len(candidates) >= limit:
                        break
            browser.close()
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300]}
    return {
        "status": "ok" if candidates else "not_found",
        "candidates": candidates,
        "provider": "browser",
    }


def fetch_instagram_user(handle: str) -> dict[str, Any]:
    handle = (handle or "").lstrip("@")
    if not configured("instagram"):
        return {"status": "skipped", "reason": "no instagram browser session"}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "skipped", "reason": "playwright not installed"}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(_state_path("instagram")))
            page = context.new_page()
            url = f"https://www.instagram.com/{handle}/"
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if "login" in page.url.lower():
                browser.close()
                return {"status": "error", "error": "instagram session expired"}
            # Meta OG tags
            photo = None
            try:
                photo = page.locator('meta[property="og:image"]').first.get_attribute("content")
            except Exception:
                pass
            title = None
            try:
                title = page.locator('meta[property="og:title"]').first.get_attribute("content")
            except Exception:
                pass
            desc = None
            try:
                desc = page.locator('meta[property="og:description"]').first.get_attribute("content")
            except Exception:
                pass
            browser.close()
        profile = {
            "username": handle,
            "full_name": (title or "").split("(")[0].strip() or None,
            "biography": desc,
            "profile_pic_url": photo,
        }
        return {
            "status": "ok",
            "handle": handle,
            "profile_url": url,
            "profile": profile,
            "recent_posts": [],
            "provider": "browser",
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300]}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Browser social session helper")
    sub = parser.add_subparsers(dest="cmd")
    login_p = sub.add_parser("login")
    login_p.add_argument("platform", choices=["instagram", "facebook", "twitter", "x"])
    login_p.add_argument("--headed", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "login":
        out = login(args.platform, headless=not args.headed)
        print(json.dumps(out, indent=2))
        return 0 if out.get("status") == "ok" else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
