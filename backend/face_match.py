"""Face / photo matching via Gemini vision.

Compares a reference headshot (usually LinkedIn) to candidate profile photos
(Instagram, etc.). No local face-embedding stack — uses the same Gemini key
already required for research.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, List, Optional

import requests
from google import genai
from google.genai import types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_VISION_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
TIMEOUT = 12
MAX_CANDIDATES = 8


def compare_faces(
    reference_photo_url: str,
    candidates: List[dict],
    *,
    person_name: Optional[str] = None,
) -> dict[str, Any]:
    """Rank candidates by visual likeness to the reference photo.

    Each candidate dict should include:
      - id / handle / username
      - photo_url (profile picture)

    Returns:
      status, rankings[{handle, score 0-100, same_person, reason}], best, ...
    """
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    ref_url = (reference_photo_url or "").strip()
    if not ref_url.startswith("http"):
        return {"status": "skipped", "reason": "no reference photo URL"}

    usable = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        pic = (c.get("photo_url") or c.get("profile_pic_url") or "").strip()
        handle = (c.get("handle") or c.get("username") or c.get("id") or "").strip()
        if not pic.startswith("http") or not handle:
            continue
        usable.append({**c, "handle": handle.lstrip("@"), "photo_url": pic})
        if len(usable) >= MAX_CANDIDATES:
            break

    if not usable:
        return {"status": "skipped", "reason": "no candidate photos to compare"}

    ref_bytes, ref_mime = _download_image(ref_url)
    if not ref_bytes:
        return {"status": "error", "error": "could not download reference photo"}

    cand_parts: list = []
    meta_lines = []
    for i, c in enumerate(usable):
        img, mime = _download_image(c["photo_url"])
        if not img:
            continue
        label = f"CANDIDATE_{len(meta_lines)}"
        meta_lines.append(
            f"{label}: handle=@{c['handle']} full_name={c.get('full_name') or ''} "
            f"url={c.get('profile_url') or ''}"
        )
        cand_parts.append(types.Part.from_text(text=f"{label} profile photo:"))
        cand_parts.append(types.Part.from_bytes(data=img, mime_type=mime or "image/jpeg"))

    if not cand_parts:
        return {"status": "error", "error": "could not download any candidate photos"}

    name_bit = f" The person is named {person_name}." if person_name else ""
    prompt = (
        "You are verifying whether Instagram (or other) profile photos show the SAME person "
        f"as the REFERENCE headshot (usually LinkedIn).{name_bit}\n\n"
        "REFERENCE photo is image #1. Then each CANDIDATE_N photo follows.\n"
        f"Candidates:\n" + "\n".join(meta_lines) + "\n\n"
        "For each candidate, score visual likeness 0–100 (100 = clearly same face).\n"
        "Ignore clothing, background, filters lightly; focus on face geometry.\n"
        "If a photo has no clear face, score low and set same_person false.\n\n"
        "Respond ONLY with JSON:\n"
        '{"rankings":[{"label":"CANDIDATE_0","score":0,"same_person":false,"reason":"string"}]}\n'
        "Include every candidate label exactly once."
    )

    contents: list = [
        types.Part.from_text(text=prompt),
        types.Part.from_text(text="REFERENCE photo:"),
        types.Part.from_bytes(data=ref_bytes, mime_type=ref_mime or "image/jpeg"),
        *cand_parts,
    ]

    try:
        client = genai.Client(api_key=api_key)
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=1024,
            ),
        )
        text = (response.text or "").strip()
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:300]}

    parsed = _parse_json(text)
    if not parsed or not isinstance(parsed.get("rankings"), list):
        return {"status": "error", "error": "could not parse face-match JSON", "raw": text[:500]}

    rankings = []
    for row in parsed["rankings"]:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "")
        m = re.search(r"(\d+)", label)
        if not m:
            continue
        idx = int(m.group(1))
        if idx < 0 or idx >= len(usable):
            continue
        c = usable[idx]
        score = _clamp_score(row.get("score"))
        same = bool(row.get("same_person")) and score >= 55
        rankings.append(
            {
                "handle": c["handle"],
                "full_name": c.get("full_name"),
                "photo_url": c["photo_url"],
                "profile_url": c.get("profile_url") or f"https://www.instagram.com/{c['handle']}/",
                "score": score,
                "same_person": same,
                "reason": (row.get("reason") or "")[:200],
            }
        )

    rankings.sort(key=lambda r: (-r["score"], -int(r["same_person"])))
    best = rankings[0] if rankings else None
    accepted = None
    if best and best["same_person"] and best["score"] >= 70:
        accepted = best

    print(
        f"  [face_match] ranked={len(rankings)} best="
        f"{(best or {}).get('handle')}@{(best or {}).get('score')} "
        f"accepted={bool(accepted)}",
        flush=True,
    )
    return {
        "status": "ok",
        "reference_photo_url": ref_url,
        "rankings": rankings,
        "best": best,
        "accepted": accepted,
        "match_mode": "exact" if accepted else ("probable" if rankings else "none"),
    }


def _download_image(url: str) -> tuple[Optional[bytes], Optional[str]]:
    try:
        resp = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": "ConnectDeeply/1.0 (face-match)"},
        )
        if resp.status_code >= 400 or not resp.content:
            return None, None
        ctype = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
        if not ctype.startswith("image/"):
            # Some CDNs return octet-stream
            ctype = "image/jpeg"
        # Cap size ~4MB
        if len(resp.content) > 4_000_000:
            return None, None
        return resp.content, ctype
    except requests.RequestException:
        return None, None


def _clamp_score(raw: Any) -> int:
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
