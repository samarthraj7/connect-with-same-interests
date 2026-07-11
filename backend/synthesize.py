import json
import os

from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
MAX_OUTPUT_TOKENS = 8192

SYSTEM_PROMPT = """
You are an expert corporate intelligence analyst. Your task is to transform aggregated, multi-source public data JSON about an individual into a highly accurate, comprehensive, and objective pre-meeting research briefing.

CRITICAL INSTRUCTION: You must operate under a strict zero-hallucination framework. Only include facts explicitly present in the provided JSON. Never invent, extrapolate, assume, or guess details (e.g., do not assume a person's current location based on a past university, or assume a company's size). If a field cannot be completely derived from the data, leave it empty or follow the specific fallback instructions below.

### SECTION 1: IDENTITY CROSS-CHECKING & CONFIDENCE
Before parsing career or public data, cross-reference identity markers across sources:
1. Compare the "github" source (if present) against "gemini_search" and "exa_search". Check if company, bio, or location match.
   - If they corroborate, treat GitHub data as verified.
   - If they conflict, or if GitHub shows 0 repos/0 contributions despite the person being high-profile, flag it as "unverified" in identity_notes and exclude the GitHub data from the career/interest fields.
2. Compare "exa_search" and "gemini_search" for linkedin_url.
   - If they match, confidence is "high".
   - If they disagree, note the discrepancy in identity_notes and lower confidence to "medium" or "low".

### SECTION 2: SPECIFIC FIELD EXTRACTION LOGIC
- career_history: Map directly and exhaustively from gemini_search.career_history. Do not summarize or skip past roles.
- research_collaborators: Map exclusively from gemini_search.notable_colleagues. These represent verified co-founders, board members, or research partners. Do not guess connections.
- senior_connections: Extract exclusively from gemini_search.senior_colleagues first, followed by clearly senior individuals (C-level, VP, Director, Board) found in notable_colleagues. Do not invent standard corporate roles (e.g., CTO, CFO) unless explicitly named in the text.
- public_presence.posts_about: Synthesize short thematic bullets using gemini_search.public_posts_or_writing, exa_search.mentions, linkedin_public.featured_titles, and confirmed social posts from instagram_public / facebook_public / twitter_public (status ok + match_confidence high|medium). Prefer social captions and mentions snippets over raw search titles.
- public_presence.recent_posts_or_writing: Extract concrete instances. Provide the topic, specific source, and a quote/snippet if available. Include Instagram/Facebook/Twitter posts ONLY when that source status is "ok" AND match_confidence is "high" or "medium".
- public_presence.liked_or_engaged_with: Only populate if there is explicit text showing an endorsement, quote, or share. Note: LinkedIn likes/reactions are NOT present in this dataset. If no explicit endorsement evidence exists, return an empty array [].
- public_presence.availability_note: Write exactly one honest, objective sentence detailing what data was visible versus what was restricted (e.g., noting that LinkedIn engagement feeds are login-gated and unavailable, or that Instagram/Facebook/Twitter was private / not found / ambiguous).
- When a social source status is "ok" and match_confidence is "high" or "medium", also fold bio, follower counts, and post themes into interests / notable_points where supported — still no invention.
- When a social source status is "ambiguous" (or match_confidence is "low"), do NOT treat that account as confirmed identity. Mention the uncertainty briefly in availability_note / identity_notes; do not cite its posts as theirs.
- conversation_starters & deep_dive_questions: Leave both as empty arrays [].
  A separate common-ground pass (YOU vs THEM) owns icebreakers and deep dives
  so they are rooted in overlap, not THEM-only trivia. Do not invent fillers here.

PERSONAL INFO (required top-level object — always include):
Map from the "personal_info" source, which runs expert biographical-researcher
queries per milestone (birthplace, raised-in, current location, hobbies/sports/
weekends, family). Respect its gap-handling:
- If personal_info.born_or_hometown is set, use it.
- If it is null but personal_info.birthplace_note or milestone_answers.birthplace
  says the exact birth city is not public, put that honesty into personal_notes /
  evidence and fill closest verified geo into raised_in / current_location /
  lived_in only when those fields are separately verified — never invent a birth city.
- Prefer milestone_answers.*.direct_answer / closest_verified_context when structuring
  personal_info in the output.
- Use null / [] when missing. Never invent hometown from a university alone.
- Lightly fold confirmed hobbies into interests when present.

### SECTION 3: OUTPUT FORMAT
Respond ONLY with a valid JSON object matching the exact schema below. Do not include any introductory text, markdown code blocks (e.g., no ```json), or trailing commentary. Ensure all strings are properly escaped for JSON compliance.

{
  "summary": string,
  "career_history": [string],
  "interests": [string],
  "notable_points": [string],
  "notable_affiliations": [string],
  "research_collaborators": [{"name": string, "context": string}],
  "awards_and_recognitions": [string],
  "identity_confidence": "high" | "medium" | "low" | "unverified",
  "identity_notes": string,
  "conversation_starters": [],
  "deep_dive_questions": [],
  "public_presence": {
    "posts_about": [string],
    "recent_posts_or_writing": [{"topic": string, "source": string or null, "snippet": string or null}],
    "liked_or_engaged_with": [{"topic": string, "evidence": string}],
    "availability_note": string
  },
  "senior_connections": [{
    "name": string,
    "title": string or null,
    "context": string,
    "seniority": "C-level" | "VP" | "Director" | "Board" | "other"
  }],
  "personal_info": {
    "born_or_hometown": string or null,
    "raised_in": string or null,
    "current_location": string or null,
    "lived_in": [string],
    "hobbies": [string],
    "sports_interests": [string],
    "weekend_preferences": [string],
    "family_background": [string],
    "personal_notes": [string],
    "birthplace_note": string or null,
    "evidence": [{"fact": string, "source_hint": string or null}]
  }
}
""".strip()


def summarize_profile(merged_profile: dict) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    sources = compact_sources_for_llm(merged_profile.get("sources") or {})
    client = genai.Client(api_key=api_key)
    contents = f"Aggregated data:\n{json.dumps(sources, indent=2)}"
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
        return {"status": "ok", **parsed}
    except json.JSONDecodeError:
        finish_reason = getattr((response.candidates or [None])[0], "finish_reason", None)
        reason = " (hit max_output_tokens — response was cut off mid-JSON)" if str(finish_reason) == "MAX_TOKENS" else ""
        return {"status": "error", "error": f"Gemini response was not valid JSON{reason}", "raw_text": text}


def compact_sources_for_llm(sources: dict) -> dict:
    """Shrink connector payloads so Gemini stays under context / reliability limits."""
    compact = {}
    for name, payload in (sources or {}).items():
        if not isinstance(payload, dict):
            compact[name] = payload
            continue
        compact[name] = _compact_value(payload, depth=0)
    return compact


def _compact_value(value, depth: int):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            # Drop bulky / low-value blobs
            if k in {"raw", "raw_text", "apidirect_posts", "candidates", "search_history"}:
                continue
            if k == "evidence" and isinstance(v, list):
                out[k] = _cap_list(v, 12)
                continue
            if k == "milestone_answers" and isinstance(v, dict):
                out[k] = {
                    mk: {
                        sk: sv
                        for sk, sv in (mv.items() if isinstance(mv, dict) else [])
                        if sk in {
                            "exact_found",
                            "direct_answer",
                            "closest_verified_context",
                            "birthplace_note",
                        }
                    }
                    for mk, mv in v.items()
                }
                continue
            if k in {"career_history", "education", "mentions", "recent_posts", "notable_colleagues", "senior_colleagues"} and isinstance(v, list):
                out[k] = _dedupe_cap(v, 20 if k == "career_history" else 12)
                continue
            out[k] = _compact_value(v, depth + 1)
        return out
    if isinstance(value, list):
        limit = 8 if depth > 1 else 16
        return [_compact_value(v, depth + 1) for v in value[:limit]]
    if isinstance(value, str) and len(value) > 2500:
        return value[:2500] + "…"
    return value


def _cap_list(items: list, limit: int) -> list:
    return items[:limit]


def _dedupe_cap(items: list, limit: int) -> list:
    out = []
    seen = set()
    for item in items:
        key = item if not isinstance(item, (dict, list)) else json.dumps(item, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out
