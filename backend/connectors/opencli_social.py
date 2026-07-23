"""Agent-Reach path: shell out to OpenCLI for Instagram / Facebook / X.

Requires `opencli` on PATH and a logged-in Chrome session on the research host.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, List, Optional

from channels import opencli_bin


TIMEOUT = int(os.environ.get("OPENCLI_TIMEOUT") or "45")


def configured() -> bool:
    return bool(opencli_bin())


def _run(args: List[str]) -> dict[str, Any]:
    bin_path = opencli_bin()
    if not bin_path:
        return {"status": "skipped", "reason": "opencli not installed"}
    cmd = [bin_path, *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": "opencli timeout"}
    except FileNotFoundError:
        return {"status": "skipped", "reason": "opencli not found"}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:500]
        return {"status": "error", "error": err or f"exit {proc.returncode}"}
    text = (proc.stdout or "").strip()
    if not text:
        return {"status": "not_found", "reason": "empty opencli output"}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # YAML-ish / multi-doc — keep raw
        return {"status": "ok", "raw": text, "parsed": None}
    return {"status": "ok", "data": data}


def search_instagram(query: str, *, limit: int = 8) -> dict[str, Any]:
    result = _run(["instagram", "search", query, "-f", "json", "--limit", str(limit)])
    if result.get("status") != "ok":
        return result
    rows = _as_list(result.get("data"))
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        handle = row.get("username") or row.get("handle") or row.get("user")
        if not handle:
            continue
        candidates.append(
            {
                "handle": str(handle).lstrip("@"),
                "url": row.get("url") or f"https://www.instagram.com/{str(handle).lstrip('@')}/",
                "full_name": row.get("full_name") or row.get("name"),
                "photo_url": row.get("profile_pic_url") or row.get("avatar") or row.get("photo_url"),
                "method": "opencli",
                "title": row.get("full_name") or handle,
            }
        )
    return {"status": "ok" if candidates else "not_found", "candidates": candidates, "provider": "opencli"}


def fetch_instagram_user(handle: str) -> dict[str, Any]:
    handle = (handle or "").lstrip("@")
    result = _run(["instagram", "user", handle, "-f", "json"])
    if result.get("status") != "ok":
        # try profile subcommand
        result = _run(["instagram", "profile", handle, "-f", "json"])
    if result.get("status") != "ok":
        return result
    data = result.get("data")
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        data = {"raw": result.get("data") or result.get("raw")}
    profile = {
        "username": handle,
        "full_name": data.get("full_name") or data.get("name"),
        "biography": data.get("biography") or data.get("bio"),
        "profile_pic_url": data.get("profile_pic_url") or data.get("avatar") or data.get("photo_url"),
        "follower_count": data.get("follower_count") or data.get("followers"),
        "is_private": data.get("is_private"),
    }
    posts = data.get("recent_posts") or data.get("posts") or data.get("items") or []
    return {
        "status": "ok",
        "handle": handle,
        "profile_url": f"https://www.instagram.com/{handle}/",
        "profile": profile,
        "recent_posts": posts if isinstance(posts, list) else [],
        "provider": "opencli",
    }


def search_facebook(query: str, *, limit: int = 8) -> dict[str, Any]:
    result = _run(["facebook", "search", query, "-f", "json"])
    if result.get("status") != "ok":
        return result
    rows = _as_list(result.get("data"))
    candidates = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        url = row.get("url") or row.get("profile_url")
        if not url:
            continue
        candidates.append(
            {
                "url": url,
                "handle": row.get("username") or row.get("id"),
                "full_name": row.get("name") or row.get("full_name"),
                "photo_url": row.get("photo_url") or row.get("avatar"),
                "method": "opencli",
            }
        )
    return {"status": "ok" if candidates else "not_found", "candidates": candidates, "provider": "opencli"}


def search_twitter(query: str, *, limit: int = 8) -> dict[str, Any]:
    # OpenCLI may expose twitter or x
    result = _run(["twitter", "search", query, "-f", "json"])
    if result.get("status") not in ("ok",):
        result = _run(["x", "search", query, "-f", "json"])
    if result.get("status") != "ok":
        return result
    rows = _as_list(result.get("data"))
    candidates = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        handle = row.get("username") or row.get("screen_name") or row.get("handle")
        if not handle:
            continue
        candidates.append(
            {
                "handle": str(handle).lstrip("@"),
                "url": row.get("url") or f"https://x.com/{str(handle).lstrip('@')}",
                "full_name": row.get("name") or row.get("full_name"),
                "photo_url": row.get("profile_image_url") or row.get("avatar"),
                "method": "opencli",
            }
        )
    return {"status": "ok" if candidates else "not_found", "candidates": candidates, "provider": "opencli"}


def _as_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "users", "items", "data", "candidates"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return []
