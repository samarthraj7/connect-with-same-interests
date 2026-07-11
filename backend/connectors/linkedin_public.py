import os
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

TIMEOUT = 8
HEADERS = {"User-Agent": "ConnectDeeplyBot/0.1 (+person-lookup prototype; single page, logged-out)"}


def fetch_linkedin_public(url: Optional[str]) -> dict:
    """Fetch ONE specific, already-known LinkedIn profile URL — logged-out,
    single request,. LinkedIn server-renders the
    profile summary for SEO, but its
    activity/post feed  back to the profile when not logged in —
    confirmed by hand.

    Off by default: set ENABLE_LINKEDIN_PUBLIC_READ=true in .env to turn it
    on. This is the "amber" higher-legal-risk connector from the project
    plan — see Connect_Deeply_Plan.pdf before enabling it broadly."""
    if not url:
        return {"status": "skipped", "reason": "no linkedin url"}
    if os.environ.get("ENABLE_LINKEDIN_PUBLIC_READ", "").lower() != "true":
        return {"status": "skipped", "reason": "ENABLE_LINKEDIN_PUBLIC_READ not set to true in .env"}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return {"status": "blocked", "url": resp.url, "http_status": resp.status_code}

        soup = BeautifulSoup(resp.text, "html.parser")

        def og(prop: str) -> Optional[str]:
            tag = soup.find("meta", property=prop)
            return tag.get("content") if tag else None

        # LinkedIn embeds some public "featured" content (articles/posts they chose to
        # feature) as inline JSON — this is NOT the general activity feed, just whatever
        # they've pinned to their profile for public view.
        featured_titles = list(dict.fromkeys(re.findall(r'"headline":"([^"]{5,120})"', resp.text)))[:5]

        headline = og("og:title")
        about = og("og:description")
        if not headline and not about and not featured_titles:
            return {"status": "no_public_data", "url": resp.url}

        return {
            "status": "ok",
            "url": resp.url,
            "headline": headline,
            "about": about,
            "featured_titles": featured_titles,
            "note": "profile summary + featured content only — the post/activity feed "
            "redirects to a login wall and was not accessed",
        }
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)}
