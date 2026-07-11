"""Shared same-person verifier for social profiles fetched via ScrapeCreators."""

from __future__ import annotations

import json
import os
from typing import Optional

from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"

VERIFY_PROMPT = """You are an identity-matching analyst. Decide if this social profile \
belongs to the TARGET person. Use only the evidence given — never invent a match \
from a similar-looking username alone.

TARGET identity:
{target}

SOCIAL profile snapshot:
{profile}

Respond with strict JSON only, no markdown fences:
{{
  "match": boolean,
  "confidence": "high" | "medium" | "low",
  "score": number,
  "reasons": [string],
  "red_flags": [string],
  "summary": string
}}
Prefer high/medium only when full_name/bio/website/location corroborate the target.
"""


def verify_social_profile(target: dict, snapshot: dict) -> Optional[dict]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    profile = snapshot.get("profile") or {}
    captions = [
        (p.get("caption") or p.get("snippet") or "")[:160]
        for p in (snapshot.get("recent_posts") or [])[:4]
        if (p.get("caption") or p.get("snippet"))
    ]
    payload = {
        "handle": snapshot.get("handle"),
        "url": snapshot.get("url"),
        "fetch_status": snapshot.get("fetch_status"),
        "full_name": profile.get("full_name") or profile.get("name"),
        "biography": profile.get("biography") or profile.get("bio") or profile.get("description"),
        "external_url": profile.get("external_url") or profile.get("website") or profile.get("url"),
        "location": profile.get("location"),
        "followers": profile.get("followers") or profile.get("followers_count") or profile.get("follower_count"),
        "is_verified": profile.get("is_verified") or profile.get("verified"),
        "is_private": profile.get("is_private"),
        "recent_captions": captions,
    }
    print(f"  [social] verifying @{snapshot.get('handle') or snapshot.get('url')}")
    try:
        client = genai.Client(api_key=api_key)
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=VERIFY_PROMPT.format(
                target=json.dumps(target, indent=2),
                profile=json.dumps(payload, indent=2),
            ),
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        data = json.loads(response.text or "")
        if not isinstance(data, dict):
            return None
        return data
    except (errors.ClientError, errors.ServerError, json.JSONDecodeError, TypeError, ValueError):
        return None
