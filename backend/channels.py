"""Agent-Reach-style channel registry with ordered backends + doctor().

Capabilities: instagram, facebook, twitter, web_read, people_search.
Social order (plan): OpenCLI → Playwright session → ScrapeCreators API.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable, List, Optional


def social_crawl_mode() -> str:
    return (os.environ.get("SOCIAL_CRAWL") or "auto").strip().lower()


def opencli_bin() -> Optional[str]:
    configured = (os.environ.get("OPENCLI_BIN") or "").strip()
    if configured and Path(configured).exists():
        return configured
    return shutil.which("opencli")


def browser_state_dir() -> Path:
    raw = (os.environ.get("BROWSER_SOCIAL_STATE_DIR") or "").strip()
    if raw:
        p = Path(raw)
    else:
        p = Path(__file__).resolve().parent / "browser_sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def scrapecreators_configured() -> bool:
    return bool((os.environ.get("SCRAPECREATORS_API_KEY") or "").strip())


def doctor() -> dict[str, Any]:
    """Internal diagnostics — which social/web backends are live."""
    oc = opencli_bin()
    state = browser_state_dir()
    return {
        "social_crawl_mode": social_crawl_mode(),
        "opencli": {"available": bool(oc), "path": oc},
        "browser_session": {
            "dir": str(state),
            "instagram": (state / "instagram.json").exists(),
            "facebook": (state / "facebook.json").exists(),
            "twitter": (state / "twitter.json").exists(),
        },
        "scrapecreators": scrapecreators_configured(),
        "exa": bool((os.environ.get("EXA_API_KEY") or "").strip()),
        "gemini": bool((os.environ.get("GEMINI_API_KEY") or "").strip()),
        "jina": bool((os.environ.get("JINA_API_KEY") or "").strip()),
    }


def backends_for(capability: str) -> List[str]:
    mode = social_crawl_mode()
    if capability in ("instagram", "facebook", "twitter"):
        if mode == "opencli":
            return ["opencli"]
        if mode == "browser":
            return ["browser"]
        if mode == "api":
            return ["scrapecreators"]
        # auto
        order = []
        if opencli_bin():
            order.append("opencli")
        if (browser_state_dir() / f"{capability if capability != 'twitter' else 'twitter'}.json").exists():
            order.append("browser")
        elif capability == "twitter" and (browser_state_dir() / "x.json").exists():
            order.append("browser")
        order.append("scrapecreators")
        return order
    if capability == "web_read":
        return ["requests", "jina", "nimble"]
    if capability == "people_search":
        return ["exa", "gemini"]
    return []


def run_with_failover(
    capability: str,
    handlers: dict[str, Callable[[], Any]],
) -> dict[str, Any]:
    """Try backends in order; return first non-empty success-like result."""
    errors = []
    for name in backends_for(capability):
        fn = handlers.get(name)
        if not fn:
            continue
        try:
            result = fn()
            if not isinstance(result, dict):
                continue
            status = result.get("status")
            if status in ("ok", "ambiguous", "partial"):
                result = dict(result)
                result["_backend"] = name
                return result
            if status in ("skipped", "not_found"):
                errors.append({name: status})
                continue
            errors.append({name: result.get("error") or status})
        except Exception as exc:
            errors.append({name: str(exc)[:200]})
    return {
        "status": "not_found",
        "reason": "all_backends_failed",
        "backend_errors": errors,
        "_capability": capability,
    }
