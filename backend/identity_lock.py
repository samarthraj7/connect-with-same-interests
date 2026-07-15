"""Canonical person identity helpers — keep research pinned to one LinkedIn profile."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse


def normalize_linkedin_url(url: Optional[str]) -> Optional[str]:
    if not url or not str(url).strip():
        return None
    u = str(url).strip()
    if not u.startswith("http"):
        u = "https://" + u.lstrip("/")
    u = u.split("?")[0].split("#")[0].rstrip("/")
    u = re.sub(r"https?://([a-z]{2}\.)?linkedin\.com", "https://www.linkedin.com", u, flags=re.I)
    if "linkedin.com/in/" not in u.lower():
        return None
    return u


def linkedin_slug(url: Optional[str]) -> Optional[str]:
    norm = normalize_linkedin_url(url)
    if not norm:
        return None
    path = urlparse(norm).path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "in":
        return parts[1].lower()
    return None


def same_linkedin(a: Optional[str], b: Optional[str]) -> bool:
    sa, sb = linkedin_slug(a), linkedin_slug(b)
    if not sa or not sb:
        return False
    return sa == sb


def identity_lock_text(
    *,
    name: str,
    linkedin_url: Optional[str] = None,
    company: Optional[str] = None,
    university: Optional[str] = None,
) -> str:
    """Hard instructions for LLM search angles — pins to one person."""
    li = normalize_linkedin_url(linkedin_url)
    lines = [
        "IDENTITY LOCK (hard filter — do not violate):",
        f'- Target full name: "{name}"',
    ]
    if li:
        lines.append(f"- Canonical LinkedIn profile URL: {li}")
        lines.append(
            "- ONLY report facts about this exact LinkedIn identity. "
            "If search results are about a different person who shares the same name, discard them entirely."
        )
        lines.append(
            f"- Prefer pages that mention, link to, or clearly belong to {li}. "
            "Do not merge careers, schools, or posts from other same-name people."
        )
    if company:
        lines.append(f'- Target company/org (when known): "{company}"')
    if university:
        lines.append(f'- Target university/school (when known): "{university}"')
    if not li and not company and not university:
        lines.append(
            "- Disambiguation is weak — only include facts you can clearly tie to one person; "
            "if multiple people match, prefer the best-supported single identity and note ambiguity."
        )
    return "\n".join(lines)
