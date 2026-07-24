"""Internal overlap engine → public conversation ideas.

Overlap matching stays server-side. What users see is *what to talk about*:
topics, openers, and deep questions — never scores or "you vs them" plumbing.
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

TOKEN_COST_BASIC = 1
TOKEN_COST_DETAILED = 3

SYSTEM_PROMPT = """
You are an expert conversation designer for high-trust professional meetings.

You receive two JSON blobs used ONLY as private context:
1) YOU — the searcher's profile
2) THEM — a researched public briefing about the other person

Internally, find real shared or closely related points. Then translate those into
interesting, engaging things to talk about. The end user should never see
"overlap analysis" — they should see conversation fuel.

### HARD RULES (zero hallucination)
- Only claim a shared bridge when BOTH sides have supporting evidence in the JSON.
- Never invent shared schools, cities, employers, hobbies, or mutual contacts.
- Prefer specific, nameable bridges over vague vibes ("both like technology").
- Respect YOU.avoid_topics.
- Openers and deep questions MUST be rooted in the talk topics you list.
- Do not write openers that announce "we have so much in common" or expose the
  matching machinery. Write natural things YOU could actually say.

### PRIORITIZE LIVED INTERESTS & DEEP BRIDGES
- Actively mine YOU.hobbies, YOU.sports, YOU.interests AND THEM.personal_info.hobbies /
  sports_interests / side_hustles / interesting_facts / interests / public writing for conversation fuel.
- Prefer place, hometown, family background, shared university, shared company, and
  children's schools (only when BOTH sides evidence them) over LinkedIn headline restatements.
- Prefer a specific shared hobby/sport/interest over a generic job-title bridge when both exist.
- Portfolios, personal sites, talks, papers, and writing on THEM are fair game.
- When THEM.research_collaborators or THEM.senior_connections overlap with YOU's collaborators /
  network (named people on both sides), create talk_about + openers about that shared person.
- If THEM.user_supplied_facts includes private_journal_snippets / private_hobbies, treat them as
  real THEM-side evidence for bridges — but never quote or reveal that these came from a private journal
  in openers (write natural conversation, not "I read your private blog").

### SPARSE THEM (students / low public footprint)
If THEM.profile_density is "sparse" OR THEM.user_supplied_facts is the main signal:
- Still produce useful conversation fuel from whatever is verified.
- Prefer curiosity about their known school, role, project, or city — not fake overlap.
- Bridges may be one-sided: YOU's experience as a natural entry into THEIR thin context
  (e.g. YOU mentored at that university) ONLY when YOU's side is evidenced.
- Set needs_more_info to concrete asks (LinkedIn URL, Instagram, major, graduation year).
- Keep talk_about shorter (2–4) rather than inventing filler.

### WHAT TO PRODUCE
1) _internal_bridges — private reasoning only (used for quality control). Each:
   - point, you_side, them_side, strength (strong|moderate|weak)
2) talk_about — USER-FACING topics (2–6). Each:
   - topic: short engaging label (not "Overlap: X")
   - hook: 1–2 sentences on why this is interesting to discuss with THEM
3) related_topics — adjacent themes worth exploring
4) openers — 4–6 light, spoken-tone icebreakers YOU could say out loud.
   Each opener MUST:
   - Name ONE concrete shared artifact from BOTH sides (city, hobby, school,
     company, collaborator, paper, sport, team, children's school, club).
   - Sound like a real first sentence in a hallway — under ~25 words.
   - Include a tiny YOU anchor ("I also…", "when I was at…") so it isn't just
     "I saw your LinkedIn".
   Ban: "What brought you to tech?", "I'd love to connect", "we have so much
   in common", "I noticed we both…", empty compliments. Prefer grounded lines
   like "You were at Stanford around the same time I was in CS — did you catch
   the HCI seminars?" when BOTH sides support it.
5) deep_questions — 4–6 substantive questions (or fewer if THEM is sparse)
   that dig into the same concrete bridges — not LinkedIn-headline restatements.
6) conversation_brief — 2–3 sentences: best overall angle for the meeting
7) message_angle — one short LinkedIn / intro note premise
8) your_profile_gaps — what YOU should add to improve future conversation ideas
9) needs_more_info — list of specific facts/handles that would unlock better topics
10) _overlap_score — integer 0–100, INTERNAL ONLY
11) _profile_density — "rich" | "ok" | "sparse"

### OUTPUT
Respond ONLY with valid JSON (no markdown fences):

{
  "_overlap_score": number,
  "_profile_density": "rich" | "ok" | "sparse",
  "_internal_bridges": [{
    "point": string,
    "you_side": string,
    "them_side": string,
    "strength": "strong" | "moderate" | "weak"
  }],
  "conversation_brief": string,
  "talk_about": [{"topic": string, "hook": string}],
  "related_topics": [string],
  "openers": [string],
  "deep_questions": [string],
  "message_angle": string,
  "your_profile_gaps": [string],
  "needs_more_info": [string]
}
""".strip()


def analyze_common_ground(
    them_summary: dict[str, Any],
    *,
    them_name: Optional[str] = None,
    user_profile: Optional[dict[str, Any]] = None,
    them_sources: Optional[dict[str, Any]] = None,
    them_hints: Optional[dict[str, Any]] = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run the internal overlap engine; returns full (including private) fields."""
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
            "reason": "user profile needs more detail (name + a few fields)",
        }

    from sparse_profile import briefing_density

    you = profile_for_overlap(profile)
    them = _them_brief(them_summary, them_sources, them_name=them_name, them_hints=them_hints)
    density = briefing_density(them_summary)
    them["profile_density"] = density

    if verbose:
        print("  [overlap] comparing YOU vs THEM (internal step)…")
        print(f"  [overlap] YOU fields: {', '.join(sorted(you.keys()))}")
        print(f"  [overlap] THEM density: {density}")
        if them_hints:
            print(f"  [overlap] extra facts about THEM: {list(them_hints.keys())}")
        print("  [overlap] calling Gemini for talk topics / openers…")

    client = genai.Client(api_key=api_key)
    contents = (
        "YOU (searcher profile — private context):\n"
        f"{json.dumps(you, indent=2)}\n\n"
        "THEM (researched person briefing — private context):\n"
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

    normalized = _normalize_engine_output(parsed)
    normalized.setdefault("_profile_density", density)
    if verbose:
        bridges = normalized.get("_internal_bridges") or []
        print(f"  [overlap] internal bridges found: {len(bridges)}")
        for b in bridges[:5]:
            if isinstance(b, dict):
                print(f"      • {b.get('point')} [{b.get('strength')}]")
        print(f"  [overlap] talk topics: {len(normalized.get('talk_about') or [])}")
        print(f"  [overlap] openers: {len(normalized.get('openers') or [])}")
        if normalized.get("needs_more_info"):
            print("  [overlap] needs more info:")
            for item in normalized["needs_more_info"][:5]:
                print(f"      • {item}")

    return {
        "status": "ok",
        "usage_tier": "detailed",
        "tokens_charged": TOKEN_COST_DETAILED,
        "you_name": you.get("name"),
        "them_name": them.get("name"),
        **normalized,
    }


def public_conversation(engine: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Strip internal overlap fields; shape the user-facing conversation block."""
    if not isinstance(engine, dict):
        return {"status": "missing"}
    if engine.get("status") and engine.get("status") != "ok":
        return {
            "status": engine.get("status"),
            "reason": engine.get("reason") or engine.get("error"),
        }

    # Support both new engine shape and older stored common_ground blobs.
    talk_about = engine.get("talk_about")
    if not talk_about:
        talk_about = []
        for g in engine.get("common_grounds") or []:
            if not isinstance(g, dict):
                continue
            talk_about.append(
                {
                    "topic": g.get("point") or "",
                    "hook": g.get("why_it_matters") or "",
                }
            )

    openers = engine.get("openers") or engine.get("conversation_starters") or []
    deep = engine.get("deep_questions") or engine.get("deep_dive_questions") or []
    related = engine.get("related_topics") or engine.get("related_topics_to_discuss") or []
    brief = engine.get("conversation_brief") or engine.get("overlap_summary") or ""
    angle = engine.get("message_angle") or engine.get("outreach_angle") or ""

    return {
        "status": "ok",
        "conversation_brief": brief,
        "talk_about": talk_about,
        "related_topics": related,
        "openers": openers,
        "deep_questions": deep,
        "message_angle": angle,
        "needs_more_info": engine.get("needs_more_info") or [],
        "profile_density": engine.get("_profile_density"),
    }


def apply_overlap_to_summary(summary: dict[str, Any], overlap: dict[str, Any]) -> dict[str, Any]:
    """Attach user-facing conversation ideas; keep full engine result for storage."""
    if overlap.get("status") != "ok":
        return summary
    updated = dict(summary)
    conv = public_conversation(overlap)
    updated["conversation_starters"] = conv.get("openers") or []
    updated["deep_dive_questions"] = conv.get("deep_questions") or []
    updated["conversation"] = conv
    # Keep a compact internal snapshot on the summary for debugging / refinement.
    updated["_conversation_engine"] = {
        "_overlap_score": overlap.get("_overlap_score") or overlap.get("overlap_score"),
        "_internal_bridges": overlap.get("_internal_bridges")
        or overlap.get("common_grounds")
        or [],
        "your_profile_gaps": overlap.get("your_profile_gaps") or [],
    }
    return updated


def _normalize_engine_output(parsed: dict[str, Any]) -> dict[str, Any]:
    """Accept new schema; also map legacy field names if the model slips."""
    score = parsed.get("_overlap_score")
    if score is None:
        score = parsed.get("overlap_score")

    bridges = parsed.get("_internal_bridges")
    if bridges is None:
        bridges = parsed.get("common_grounds") or []

    talk = parsed.get("talk_about")
    if not talk and parsed.get("common_grounds"):
        talk = [
            {"topic": g.get("point", ""), "hook": g.get("why_it_matters", "")}
            for g in parsed["common_grounds"]
            if isinstance(g, dict)
        ]

    openers = list(parsed.get("openers") or parsed.get("conversation_starters") or [])
    # Drop openers that don't share any meaningful token with bridge points
    bridge_blob = " ".join(
        str((b or {}).get("point") or "")
        for b in (bridges or [])
        if isinstance(b, dict)
    ).lower()
    if bridge_blob and openers:
        filtered = []
        for o in openers:
            text = str(o).lower()
            tokens = [t for t in text.replace("?", " ").split() if len(t) > 3]
            if any(t in bridge_blob for t in tokens[:8]) or len(filtered) < 2:
                filtered.append(o)
        openers = filtered[:6] or openers[:4]

    return {
        "_overlap_score": score,
        "_internal_bridges": bridges or [],
        "conversation_brief": parsed.get("conversation_brief") or parsed.get("overlap_summary") or "",
        "talk_about": talk or [],
        "related_topics": parsed.get("related_topics")
        or parsed.get("related_topics_to_discuss")
        or [],
        "openers": openers,
        "deep_questions": parsed.get("deep_questions") or parsed.get("deep_dive_questions") or [],
        "message_angle": parsed.get("message_angle") or parsed.get("outreach_angle") or "",
        "your_profile_gaps": parsed.get("your_profile_gaps") or [],
        "needs_more_info": parsed.get("needs_more_info") or [],
        "_profile_density": parsed.get("_profile_density"),
        # Legacy aliases so older CLI/storage paths keep working during transition.
        "overlap_score": score,
        "overlap_summary": parsed.get("conversation_brief") or parsed.get("overlap_summary") or "",
        "common_grounds": bridges or [],
        "related_topics_to_discuss": parsed.get("related_topics")
        or parsed.get("related_topics_to_discuss")
        or [],
        "conversation_starters": openers,
        "deep_dive_questions": parsed.get("deep_questions") or parsed.get("deep_dive_questions") or [],
        "outreach_angle": parsed.get("message_angle") or parsed.get("outreach_angle") or "",
    }


def _them_brief(
    summary: dict[str, Any],
    sources: Optional[dict[str, Any]],
    *,
    them_name: Optional[str] = None,
    them_hints: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
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
        "identity_confidence": summary.get("identity_confidence"),
        "personal_info": {
            k: personal.get(k)
            for k in (
                "born_or_hometown",
                "raised_in",
                "current_location",
                "lived_in",
                "hobbies",
                "sports_interests",
                "side_hustles",
                "interesting_facts",
                "weekend_preferences",
                "family_background",
                "spouse",
                "children",
                "siblings",
                "estimated_age_band",
                "estimated_age_basis",
                "personal_notes",
            )
            if personal.get(k)
        },
        "family": summary.get("family")
        or {
            "spouse": personal.get("spouse"),
            "children": personal.get("children") or [],
            "siblings": personal.get("siblings") or [],
            "notes": personal.get("family_background") or [],
        },
        "relative_profiles": (sources or {}).get("personal_info", {}).get("relative_profiles")
        if isinstance((sources or {}).get("personal_info"), dict)
        else [],
        "research_collaborators": summary.get("research_collaborators") or [],
        "senior_connections": summary.get("senior_connections") or [],
        "estimated_age_band": summary.get("estimated_age_band") or personal.get("estimated_age_band"),
    }
    gemini = (sources or {}).get("gemini_search") or {}
    if isinstance(gemini, dict):
        if gemini.get("education"):
            brief["education"] = gemini["education"]
        if gemini.get("current_role"):
            brief["current_role"] = gemini["current_role"]
        if gemini.get("current_company"):
            brief["current_company"] = gemini["current_company"]
        if gemini.get("notable_colleagues") and not brief.get("research_collaborators"):
            brief["research_collaborators"] = gemini["notable_colleagues"]
        if gemini.get("senior_colleagues") and not brief.get("senior_connections"):
            brief["senior_connections"] = gemini["senior_colleagues"]
    deep = (sources or {}).get("deep_agent") or {}
    if isinstance(deep, dict) and deep.get("evidence"):
        brief["deep_evidence"] = (deep.get("evidence") or [])[:12]
    pages = (sources or {}).get("nimble_pages") or (sources or {}).get("page_extracts") or {}
    if isinstance(pages, dict) and pages.get("pages"):
        brief["page_extracts"] = [
            {"url": p.get("final_url") or p.get("url"), "title": p.get("title")}
            for p in (pages.get("pages") or [])[:6]
            if isinstance(p, dict)
        ]
    # Fold user-supplied and research hobbies/sports onto top-level for the model
    hobbies = list(personal.get("hobbies") or [])
    sports = list(personal.get("sports_interests") or personal.get("sports") or [])
    for item in list(personal.get("side_hustles") or [])[:4]:
        if item and item not in hobbies:
            hobbies.append(item)
    interests = list(brief.get("interests") or [])
    for item in hobbies + sports:
        if item and item not in interests:
            interests.append(item)
    for item in (personal.get("interesting_facts") or [])[:3]:
        short = str(item).split(":", 1)[-1].strip() if isinstance(item, str) else str(item)
        if short and len(short) < 100 and short not in interests:
            interests.append(short)
    if interests:
        brief["interests"] = interests
    if hobbies:
        brief["hobbies"] = hobbies
    if sports:
        brief["sports"] = sports
    pub_web = (sources or {}).get("public_web") or {}
    if isinstance(pub_web, dict) and pub_web.get("status") == "ok":
        brief["portfolios"] = (pub_web.get("portfolios") or [])[:6]
        brief["writing_and_talks"] = (pub_web.get("writing_and_talks") or [])[:6]
    if them_hints:
        # Facts the searcher already knows (critical for sparse students).
        brief["user_supplied_facts"] = {
            k: v for k, v in them_hints.items() if v not in (None, "", [])
        }
    return brief
