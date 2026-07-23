"""Sanitize research payloads before they reach mobile/API clients.

Never expose vendor product names (Apollo, Nimble, ScrapeCreators, Enrich Layer, etc.).
"""

from __future__ import annotations

import copy
import re
from typing import Any, Optional

_VENDOR_RE = re.compile(
    r"\b(apollo|nimble|scrapecreators|scrape\s*creators|enrich\s*layer|enrichlayer|"
    r"a-?leads|exa\.ai|gemini|openai)\b",
    re.I,
)

_KEY_MAP = {
    "apollo": "enrichment",
    "aleads": "contact_enrichment",
    "enrichlayer": "profile_enrichment",
    "nimble_pages": "page_extracts",
}

_PHOTO_SOURCE_MAP = {
    "apollo": "licensed",
    "enrichlayer": "licensed",
    "licensed": "licensed",
    "opengraph": "profile",
    "profile": "profile",
    "github": "web",
    "gemini_photo_hunt": "web",
    "web": "web",
    "monogram": "placeholder",
    "placeholder": "placeholder",
}


def sanitize_error(msg: Optional[str]) -> str:
    if not msg:
        return "Research temporarily unavailable"
    cleaned = _VENDOR_RE.sub("provider", str(msg))
    if "429" in cleaned or "rate" in cleaned.lower() or "quota" in cleaned.lower():
        return "Research model temporarily unavailable. Please try again in a moment."
    return cleaned[:240]


def sanitize_sources_for_client(sources: Optional[dict]) -> dict:
    """Rename/drop vendor keys; genericize errors; keep useful fields."""
    out: dict[str, Any] = {}
    for key, payload in (sources or {}).items():
        if key.startswith("_"):
            continue
        public_key = _KEY_MAP.get(key, key)
        if not isinstance(payload, dict):
            out[public_key] = payload
            continue
        block = copy.deepcopy(payload)
        if "error" in block:
            block["error"] = sanitize_error(block.get("error"))
        if "reason" in block and isinstance(block["reason"], str):
            block["reason"] = sanitize_error(block["reason"])
        # Drop huge vendor-specific raw blobs
        for drop in ("raw", "raw_text", "html", "debug", "apidirect_posts"):
            block.pop(drop, None)
        out[public_key] = block
    return out


def sanitize_source_status(status: Optional[dict]) -> dict:
    out = {}
    for k, v in (status or {}).items():
        out[_KEY_MAP.get(k, k)] = v
    return out


def sanitize_candidates(candidates: list) -> list:
    cleaned = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        row = dict(c)
        src = (row.get("photo_source") or "").lower()
        if src:
            row["photo_source"] = _PHOTO_SOURCE_MAP.get(src, "web")
        cleaned.append(row)
    return cleaned


def public_health_flags() -> dict:
    """Vendor-free health — only generic capability flags."""
    import os

    flags = {
        "ok": True,
        "service": "connect-deeply",
        "enrichment_configured": bool(
            (os.environ.get("APOLLO_API_KEY") or "").strip()
            or (os.environ.get("ENRICHLAYER_API_KEY") or "").strip()
        ),
        "page_extract_configured": bool((os.environ.get("NIMBLE_API_KEY") or "").strip()),
        "search_configured": bool(
            (os.environ.get("EXA_API_KEY") or "").strip()
            or (os.environ.get("GEMINI_API_KEY") or "").strip()
        ),
        "calendar_configured": False,
        "social_crawl_ready": False,
    }
    try:
        from channels import doctor

        d = doctor()
        flags["social_crawl_ready"] = bool(
            d.get("opencli", {}).get("available")
            or d.get("browser_session", {}).get("instagram")
            or d.get("scrapecreators")
        )
    except Exception:
        pass
    return flags


def internal_channel_doctor() -> dict:
    from channels import doctor

    return doctor()
