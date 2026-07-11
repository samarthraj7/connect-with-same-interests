"""Common-ground / overlap layer between YOU (user_profile) and THEM (research).

Phase 2: drives conversation_starters, deep_dive_questions, and related topics
from shared context — not generic person-only prompts.

Phase 3 hooks (not fully built yet):
- token tiers: basic = research only (1), detailed = research + overlap (3)
- profile_refinement suggestions so YOUR profile improves over time
- CRM-ready contact + interaction logging via storage
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry
from user_profile import is_usable, load_user_profile, profile_for_overlap

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
MAX_OUTPUT_TOKENS = 6144

# Token ledger for the upcoming MVP (charged by CLI / API later).
TOKEN_COST_BASIC = 1
TOKEN_COST_DETAILED = 3

SYSTEM_PROMPT = """
You are an expert relationship strategist for high-trust professional conversations.

You receive two JSON blobs:
1) YOU — the searcher's living profile (who is about to meet / message someone)
2) THEM — a researched public briefing about the other person

Your job is to find REAL common ground and turn it into conversation that feels
personal and earned — never generic networking fluff.

### HARD RULES (zero hallucination)
- Only claim overlap when BOTH sides have supporting evidence in the JSON.
- If evidence is weak or one-sided, label strength "weak" or omit that point.
- Never invent shared schools, cities, employers, hobbies, or mutual contacts.
- Prefer specific, nameable overlaps (same org, same city era, same sector thesis)
  over vague vibes ("both like technology").
- Respect YOU.avoid_topics: do not suggest openers in those areas.
- Icebreakers and deep dives MUST be rooted in listed common_grounds or
  related_topics. Do not fall back to THEM-only career trivia unless there is
  at least a thin bridge from YOU (e.g. YOU invest in healthcare AND THEM works
  in healthtech → bridge is investment thesis, not "tell me about your job").

### WHAT TO PRODUCE
1) common_grounds — concrete shared or closely related points. Each item:
   - point: short label
   - you_side / them_side: the supporting facts
   - strength: strong | moderate | weak
   - why_it_matters: one sentence on conversational value
2) related_topics_to_discuss — adjacent themes worth exploring (not strict
   overlaps but natural extensions of the common ground)
3) conversation_starters — 4–6 light, rapport-building openers written as
   something YOU could actually say. First-person or natural spoken tone.
   Anchor each in a common_ground (mention the bridge implicitly).
4) deep_dive_questions — 4–6 substantive questions that use the overlap as
   the entry point, then go deeper into THEIR expertise / writing / company.
5) overlap_summary — 2–4 sentences: overall fit and best angle for YOU.
6) overlap_score — integer 0–100 (calibrated; sparse mutual facts → low).
7) your_profile_gaps — what YOU should add to your profile to improve future
   overlap detection with people like THEM (actionable, specific). This feeds
   Phase 3 profile refinement.
8) outreach_angle — one short suggested LinkedIn note / opener premise
   (no fake familiarity). Useful for Phase 3 connection requests.

### OUTPUT
Respond ONLY with valid JSON (no markdown fences):

{
  "overlap_score": number,
  "overlap_summary": string,
  "common_grounds": [{
    "point": string,
    "you_side": string,
    "them_side": string,
    "strength": "strong" | "moderate" | "weak",
    "why_it_matters": string
  }],
  "related_topics_to_discuss": [string],
  "conversation_starters": [string],
  "deep_dive_questions": [string],
  "your_profile_gaps": [string],
  "outreach_angle": string
}
""".strip()


def analyze_common_ground(
    them_summary: dict[str, Any],
    *,
    them_name: Optional[str] = None,
    user_profile: Optional[dict[str, Any]] = None,
    them_sources: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compare YOU vs THEM and return overlap + overlap-based questions."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    try:
        profile = user_profile if user_profile is not None else load_user_profile()
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "skipped", "reason": str(exc)}

    if not is_usable(profile):
        return {
            "status": "skipped",
            "reason": "user_profile.json needs more detail (name + a few fields)",
        }

    you = profile_for_overlap(profile)
    them = _them_brief(them_summary, them_sources, them_name=them_name)

    client = genai.Client(api_key=api_key)
    contents = (
        "YOU (searcher profile):\n"
        f"{json.dumps(you, indent=2)}\n\n"
        "THEM (researched person briefing):\n"
        f"{json.dumps(them, indent=2)}"
    )
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    try:
        response = generate_with_retry(client, model=MODEL, contents=contents, config=config)
    except errors.ClientError as exc:
        return {"status": "error", "error": f"Gemini client error: {exc}"}
    except errors.ServerError as exc:
        return {"status": "error", "error": f"Gemini unavailable after retries: {exc}"}

    text = response.text or ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        finish_reason = getattr((response.candidates or [None])[0], "finish_reason", None)
        reason = (
            " (hit max_output_tokens — response was cut off mid-JSON)"
            if str(finish_reason) == "MAX_TOKENS"
            else ""
        )
        return {
            "status": "error",
            "error": f"Gemini response was not valid JSON{reason}",
            "raw_text": text,
        }

    return {
        "status": "ok",
        "usage_tier": "detailed",
        "tokens_charged": TOKEN_COST_DETAILED,
        "you_name": you.get("name"),
        "them_name": them.get("name"),
        **parsed,
    }


def apply_overlap_to_summary(summary: dict[str, Any], overlap: dict[str, Any]) -> dict[str, Any]:
    """Replace icebreakers / deep dives with overlap-rooted versions when available."""
    if overlap.get("status") != "ok":
        return summary
    updated = dict(summary)
    if overlap.get("conversation_starters"):
        updated["conversation_starters"] = overlap["conversation_starters"]
    if overlap.get("deep_dive_questions"):
        updated["deep_dive_questions"] = overlap["deep_dive_questions"]
    updated["common_ground"] = {
        "overlap_score": overlap.get("overlap_score"),
        "overlap_summary": overlap.get("overlap_summary"),
        "common_grounds": overlap.get("common_grounds") or [],
        "related_topics_to_discuss": overlap.get("related_topics_to_discuss") or [],
        "your_profile_gaps": overlap.get("your_profile_gaps") or [],
        "outreach_angle": overlap.get("outreach_angle"),
        "you_name": overlap.get("you_name"),
        "usage_tier": overlap.get("usage_tier"),
        "tokens_charged": overlap.get("tokens_charged"),
    }
    return updated


def _them_brief(
    summary: dict[str, Any],
    sources: Optional[dict[str, Any]],
    *,
    them_name: Optional[str] = None,
) -> dict[str, Any]:
    """Compact THEM payload so the overlap call stays focused."""
    personal = summary.get("personal_info") if isinstance(summary.get("personal_info"), dict) else {}
    brief: dict[str, Any] = {
        "name": them_name,
        "summary": summary.get("summary"),
        "career_history": summary.get("career_history") or [],
        "interests": summary.get("interests") or [],
        "notable_points": summary.get("notable_points") or [],
        "notable_affiliations": summary.get("notable_affiliations") or [],
        "awards_and_recognitions": summary.get("awards_and_recognitions") or [],
        "public_presence": summary.get("public_presence"),
        "personal_info": {
            k: personal.get(k)
            for k in (
                "born_or_hometown",
                "raised_in",
                "current_location",
                "lived_in",
                "hobbies",
                "sports_interests",
                "weekend_preferences",
                "family_background",
                "personal_notes",
            )
            if personal.get(k)
        },
    }
    gemini = (sources or {}).get("gemini_search") or {}
    if isinstance(gemini, dict):
        if gemini.get("education"):
            brief["education"] = gemini["education"]
        if gemini.get("current_role"):
            brief["current_role"] = gemini["current_role"]
        if gemini.get("current_company"):
            brief["current_company"] = gemini["current_company"]
    return brief
