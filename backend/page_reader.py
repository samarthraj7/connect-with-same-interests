"""Fit-text page fetch + media harvest (Crawl4AI concepts, no heavy dependency).

Order: requests → optional Jina reader → basic HTML strip. Collect img/og images.
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from typing import Any, List, Optional
from urllib.parse import urljoin, urlparse

import requests

HEADERS = {"User-Agent": "ConnectDeeplyBot/0.3 (+public research; fit-text)"}
TIMEOUT = int(os.environ.get("PAGE_READ_TIMEOUT") or "12")
MAX_CHARS = int(os.environ.get("PAGE_READ_MAX_CHARS") or "12000")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = 0
        self.parts: List[str] = []
        self.images: List[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "img":
            src = attrs_d.get("src") or attrs_d.get("data-src") or ""
            if src:
                self.images.append(src)
        if tag == "meta":
            prop = (attrs_d.get("property") or attrs_d.get("name") or "").lower()
            if prop in ("og:image", "twitter:image") and attrs_d.get("content"):
                self.images.append(attrs_d["content"])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title += text + " "
        else:
            self.parts.append(text)


def read_page(url: str) -> dict[str, Any]:
    """Return fit markdown-ish text + image URLs for a public page."""
    if not url or not str(url).startswith("http"):
        return {"status": "error", "error": "bad url"}

    # 1) Jina reader when key/configured
    jina_key = (os.environ.get("JINA_API_KEY") or "").strip()
    if jina_key or os.environ.get("JINA_READER", "").strip() in ("1", "true", "yes"):
        try:
            headers = dict(HEADERS)
            if jina_key:
                headers["Authorization"] = f"Bearer {jina_key}"
            r = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=TIMEOUT)
            if r.status_code < 400 and (r.text or "").strip():
                text = (r.text or "")[:MAX_CHARS]
                return {
                    "status": "ok",
                    "url": url,
                    "backend": "jina",
                    "title": None,
                    "text": text,
                    "markdown": text,
                    "images": _extract_image_urls_from_markdown(text, url),
                }
        except requests.RequestException:
            pass

    # 2) Plain HTTP + HTML strip
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code >= 400:
            return {"status": "error", "error": f"http {resp.status_code}", "url": url}
        ctype = (resp.headers.get("Content-Type") or "").lower()
        final = str(resp.url)
        if "html" not in ctype and not resp.text.lstrip().startswith("<"):
            text = (resp.text or "")[:MAX_CHARS]
            return {
                "status": "ok",
                "url": final,
                "backend": "requests",
                "title": None,
                "text": text,
                "markdown": text,
                "images": [],
            }
        parser = _TextExtractor()
        parser.feed(resp.text or "")
        text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:MAX_CHARS]
        images = []
        for src in parser.images:
            abs_u = urljoin(final, src)
            if abs_u.startswith("http"):
                images.append(abs_u)
        # dedupe images
        seen = set()
        uniq_images = []
        for u in images:
            if u in seen:
                continue
            seen.add(u)
            uniq_images.append(u)
        return {
            "status": "ok",
            "url": final,
            "backend": "requests",
            "title": (parser.title or "").strip() or None,
            "text": text,
            "markdown": text,
            "images": uniq_images[:20],
        }
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)[:200], "url": url}


def _extract_image_urls_from_markdown(text: str, base: str) -> List[str]:
    out = []
    for m in re.finditer(r"https?://[^\s\)\"']+\.(?:png|jpg|jpeg|webp|gif)", text, re.I):
        out.append(m.group(0))
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text):
        u = urljoin(base, m.group(1))
        if u.startswith("http"):
            out.append(u)
    return out[:20]


def harvest_profile_images(url: str) -> List[str]:
    page = read_page(url)
    if page.get("status") != "ok":
        return []
    images = list(page.get("images") or [])
    # Prefer likely headshots / larger assets
    ranked = sorted(
        images,
        key=lambda u: (
            0 if any(x in u.lower() for x in ("avatar", "profile", "head", "photo", "portrait")) else 1,
            len(u),
        ),
    )
    return ranked[:8]
