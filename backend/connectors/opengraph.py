import requests
from bs4 import BeautifulSoup

TIMEOUT = 8
HEADERS = {"User-Agent": "ConnectDeeplyBot/0.1 (+person-lookup prototype)"}


def fetch_open_graph(url: str) -> dict:
    """Fetch a public page's Open Graph / meta tags — the same metadata every
    site already publishes for link-preview unfurling (Slack, iMessage,
    Twitter). Reads one specific public URL; not a crawler, not gated
    content, no login."""
    if not url:
        return {"status": "skipped", "reason": "no url"}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        tags = {}
        for tag in soup.find_all("meta"):
            key = tag.get("property") or tag.get("name")
            if key and (key.startswith("og:") or key in ("description", "twitter:description")):
                tags[key] = tag.get("content")

        title_tag = soup.find("title")
        title = tags.get("og:title") or (title_tag.text.strip() if title_tag else None)
        description = tags.get("og:description") or tags.get("description")

        # Sites like Instagram serve a near-empty JS app shell to logged-out, non-browser
        # requests — no og tags, no real <title> — so say so plainly instead of returning
        # a hollow "ok" that looks like real data.
        if not title and not description:
            return {"status": "no_public_data", "requested_url": url, "url": resp.url}

        return {
            "status": "ok",
            "requested_url": url,
            "url": resp.url,  # resolved destination — grounding/redirect links aren't the real page URL
            "title": title,
            "description": description,
            "site_name": tags.get("og:site_name"),
        }
    except requests.RequestException as exc:
        return {"status": "error", "url": url, "error": str(exc)}
