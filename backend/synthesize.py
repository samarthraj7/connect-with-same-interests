import json
import os

from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
# gemini-2.5 thinking tokens count against max_output_tokens; keep headroom for full JSON.
MAX_OUTPUT_TOKENS = int(os.environ.get("SYNTHESIZE_MAX_OUTPUT_TOKENS") or "24576")

SYSTEM_PROMPT = """
You are an expert corporate intelligence analyst. Your task is to transform aggregated, multi-source public data JSON about an individual into a highly accurate, comprehensive, and objective pre-meeting research briefing.

CRITICAL INSTRUCTION: You must operate under a strict zero-hallucination framework. Only include facts explicitly present in the provided JSON. Never invent, extrapolate, assume, or guess details (e.g., do not assume a person's current location based on a past university, or assume a company's size). If a field cannot be completely derived from the data, leave it empty or follow the specific fallback instructions below.

### SECTION 0: SINGLE-PERSON IDENTITY LOCK
Do not mix up same-name people. Re-verify every fact against the locked LinkedIn / chosen identity before including it. Create and keep one unique identity for the person chosen at Find Me / research start.
The Aggregated data includes a "query" object with the selected person's name / company / university / linkedin_url.
- Treat query.linkedin_url as the CANONICAL identity when present.
- Prefer "knowledge_graph.verified_claims" (reconciled evidence) over raw connector dumps. Do NOT auto-merge conflicted claims — list them under conflicts only.
- Discard facts that clearly belong to a different person with the same name (different LinkedIn URL, incompatible employer timeline, conflicting education).
- If gemini_search or exa_search disagree on linkedin_url, prefer query.linkedin_url and ignore conflicting source blobs.
- Prefer licensed enrichment + linkedin_public + deep_agent.evidence + page_extracts/nimble_pages.pages + sources that agree with the canonical LinkedIn / company.
- When page extracts (nimble_pages) status is "ok", treat pages[].markdown / pages[].text as high-value cited page body (each fact must still end with that page's url). Prefer pages from university directories and employer team/bio pages (mode=directed_org) for career, education, and senior colleagues.
When deep_agent.evidence is present, treat those cited facts as high-priority inputs for summary / career / interests.

### SECTION 1: IDENTITY CROSS-CHECKING & CONFIDENCE
Before parsing career or public data, cross-reference identity markers across sources:
1. Compare the "github" source (if present) against "gemini_search" and "exa_search". Check if company, bio, or location match.
   - If they corroborate, treat GitHub data as verified.
   - If they conflict, or if GitHub shows 0 repos/0 contributions despite the person being high-profile, flag it as "unverified" in identity_notes and exclude the GitHub data from the career/interest fields.
2. Compare "exa_search" and "gemini_search" for linkedin_url against query.linkedin_url.
   - If they match the query (or query has no LinkedIn), confidence is "high" when company also agrees.
   - If they disagree with the query LinkedIn, exclude the disagreeing source's career/personal facts and lower confidence.

### SECTION 1b: SOURCE CITATIONS (REQUIRED)
Every factual sentence you write in summary, career_history items, interests, notable_points,
notable_affiliations, awards_and_recognitions, public_presence.posts_about, and personal_info
personal_notes MUST end with the source URL in parentheses, drawn from the JSON
(gemini_search.sources[].url, exa_search.mentions[].url, public_web.*.url, nimble_pages.pages[].url,
apollo, linkedin_public.url, verified pages, personal_info.evidence, etc.).

Example format:
"She is an SDE Intern at Sprintray working on agentic AI backends (https://www.linkedin.com/in/rheshavinod)."
"Previously an SDE at BT Group (https://example.com/article)."

Rules:
- Put the URL at the END of the sentence, inside parentheses, with no markdown link syntax.
- Prefer the most specific page URL available for that fact.
- If multiple sources support one sentence, use the best single URL.
- If you cannot cite a URL from the provided JSON, OMIT the fact entirely — do not invent a URL and do not write uncited claims.
- For recent_posts_or_writing, set "source" to the URL string when available (not just a site name).

### SECTION 2: SPECIFIC FIELD EXTRACTION LOGIC
- career_history: Map directly and exhaustively from gemini_search.career_history (and apollo employment when present). Append a source URL to each item. Do not summarize or skip past roles. Do not blend roles from another same-name person.
- research_collaborators: Map exclusively from gemini_search.notable_colleagues. These represent verified co-founders, board members, or research partners. Do not guess connections.
- senior_connections: Extract exclusively from gemini_search.senior_colleagues first, followed by clearly senior individuals (C-level, VP, Director, Board) found in notable_colleagues. Do not invent standard corporate roles (e.g., CTO, CFO) unless explicitly named in the text.
- public_presence.posts_about: Synthesize short thematic bullets using gemini_search.public_posts_or_writing, exa_search.mentions, linkedin_public.featured_titles, and confirmed social posts from instagram_public / facebook_public / twitter_public (status ok + match_confidence high|medium). Each bullet ends with (url). Prefer social captions and mentions snippets over raw search titles.
- public_presence.recent_posts_or_writing: Extract concrete instances. Provide the topic, specific source URL, and a quote/snippet if available. Include Instagram/Facebook/Twitter posts ONLY when that source status is "ok" AND match_confidence is "high" or "medium".
- public_presence.liked_or_engaged_with: Only populate if there is explicit text showing an endorsement, quote, or share. Note: LinkedIn likes/reactions are NOT present in this dataset. If no explicit endorsement evidence exists, return an empty array [].
- public_presence.availability_note: Write exactly one honest, objective sentence detailing what data was visible versus what was restricted (e.g., noting that LinkedIn engagement feeds are login-gated and unavailable, or that Instagram/Facebook/Twitter was private / not found / ambiguous). Citations optional on this meta note.
- When a social source status is "ok" and match_confidence is "high" or "medium", also fold bio, follower counts, and post themes into interests / notable_points where supported — still no invention; end with (url).
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
- personal_notes and evidence facts should end with (url) when a source URL exists.

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
    "spouse": string or null,
    "children": [{"name": string or null, "school": string or null, "company": string or null, "note": string or null}],
    "siblings": [{"name": string or null, "note": string or null}],
    "estimated_age_band": string or null,
    "estimated_age_basis": string or null,
    "bachelors_year": number or null,
    "personal_notes": [string],
    "birthplace_note": string or null,
    "evidence": [{"fact": string, "source_hint": string or null}]
  },
  "family": {
    "spouse": string or null,
    "children": [{"name": string or null, "school": string or null, "company": string or null, "note": string or null}],
    "siblings": [{"name": string or null, "note": string or null}],
    "notes": [string]
  },
  "estimated_age_band": string or null,
  "estimated_age_basis": string or null,
  "section_confidence": {
    "career": "high" | "medium" | "low",
    "personal": "high" | "medium" | "low",
    "family": "high" | "medium" | "low",
    "social": "high" | "medium" | "low"
  },
  "citations": [{"fact": string, "url": string, "confidence": number}],
  "conflicts": [{"predicate": string, "existing": string, "incoming": string, "note": string}]
}
""".strip()


def summarize_profile(merged_profile: dict) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    from identity_lock import normalize_linkedin_url

    sources = compact_sources_for_llm(merged_profile.get("sources") or {})
    query = merged_profile.get("query") or {}
    canonical = normalize_linkedin_url(query.get("linkedin_url"))

    # Hard gate: strip same-name Peerlist/GitHub/posts that don't corroborate chosen LinkedIn
    if canonical:
        from identity_filter import filter_sources_against_linkedin

        raw = merged_profile.get("sources") or sources
        filtered = filter_sources_against_linkedin(
            linkedin_url=canonical,
            sources=raw if isinstance(raw, dict) else {},
            company=query.get("company"),
            university=query.get("university"),
            name=query.get("name"),
        )
        sources = compact_sources_for_llm(filtered)

    # Drop Exa rediscovery noise if it somehow points elsewhere
    exa = sources.get("exa_search")
    if isinstance(exa, dict) and canonical and exa.get("linkedin_url"):
        from identity_lock import same_linkedin

        if not same_linkedin(canonical, exa.get("linkedin_url")):
            print(
                f"  [synthesize] dropping mismatched exa linkedin "
                f"{exa.get('linkedin_url')!r} vs {canonical!r}",
                flush=True,
            )
            exa = dict(exa)
            exa["linkedin_url"] = canonical
            exa["identity_note"] = "linkedin_url forced to query canonical; original rediscovery discarded"
            sources["exa_search"] = exa

    identity = {
        "name": query.get("name"),
        "company": query.get("company"),
        "university": query.get("university"),
        "place": query.get("place"),
        "linkedin_url": canonical or query.get("linkedin_url"),
    }
    kg_payload = merged_profile.get("knowledge_graph_for_llm") or {}
    conflicts = merged_profile.get("conflicts") or kg_payload.get("conflicts") or []
    corrections = merged_profile.get("prior_user_corrections_text") or ""
    client = genai.Client(api_key=api_key)
    contents = (
        "Selected identity (HARD LOCK — do not mix other same-name people):\n"
        f"{json.dumps(identity, indent=2)}\n\n"
        "RULES:\n"
        "- Do not mix up identities. Re-verify against the locked LinkedIn before including a fact.\n"
        "- The LinkedIn URL above is the ONLY person you may describe.\n"
        "- Prefer knowledge_graph.verified_claims over raw dumps. Never invent.\n"
        "- Do NOT resolve conflicts yourself — copy unresolved conflicts into output.conflicts.\n"
        "- Ignore Peerlist, GitHub, blogs, or posts that belong to a different LinkedIn slug.\n"
        "- If sources conflict on school/employer/location, prefer licensed enrichment / LinkedIn-tied "
        "sources and leave the conflict in conflicts[].\n"
        "- Never invent a blended career from two same-name people.\n"
        "- Include section_confidence and citations[] with public URLs.\n\n"
        + (f"{corrections}\n\n" if corrections else "")
        + "Reconciled knowledge graph (authoritative for facts):\n"
        f"{json.dumps(kg_payload, indent=2)[:60000]}\n\n"
        f"Known conflicts (do not auto-merge):\n{json.dumps(conflicts, indent=2)[:12000]}\n\n"
        f"Aggregated data:\n{json.dumps({'query': identity, **sources}, indent=2)}"
    )
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        max_output_tokens=MAX_OUTPUT_TOKENS,
        # Disable thinking so the full output budget goes to the JSON briefing
        # (thinking still counts toward max_output_tokens on gemini-2.5).
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    from gemini_retry import user_facing_gemini_error

    try:
        response = generate_with_retry(client, model=MODEL, contents=contents, config=config)
    except (errors.ClientError, errors.ServerError) as exc:
        return {"status": "error", "error": user_facing_gemini_error(exc)}

    text = response.text or ""
    try:
        parsed = json.loads(text)
        return {"status": "ok", **_backfill_family_fields(parsed, sources)}
    except json.JSONDecodeError:
        finish_reason = None
        try:
            finish_reason = getattr((response.candidates or [None])[0], "finish_reason", None)
        except Exception:
            pass
        fr_name = getattr(finish_reason, "name", None) or str(finish_reason or "")
        hit_max = "MAX_TOKENS" in str(fr_name)
        # Retry once with a smaller payload when truncated
        if hit_max or len(text) > 100:
            try:
                slim_sources = _aggressive_compact(sources)
                slim_contents = (
                    "Selected identity (HARD LOCK):\n"
                    f"{json.dumps(identity)}\n"
                    "Return the briefing JSON schema only. Prefer omit over inventing.\n"
                    f"{json.dumps({'query': identity, **slim_sources})[:48000]}"
                )
                response2 = generate_with_retry(
                    client, model=MODEL, contents=slim_contents, config=config
                )
                parsed = json.loads(response2.text or "")
                return {"status": "ok", **_backfill_family_fields(parsed, sources)}
            except Exception:
                pass
        return {
            "status": "error",
            "error": user_facing_gemini_error(None),
        }


def _backfill_family_fields(parsed: dict, sources: dict) -> dict:
    """If the model omits family/age, copy from personal_info when present."""
    if not isinstance(parsed, dict):
        return parsed
    pi = sources.get("personal_info") if isinstance(sources.get("personal_info"), dict) else {}
    out_pi = parsed.get("personal_info") if isinstance(parsed.get("personal_info"), dict) else {}
    if not isinstance(out_pi, dict):
        out_pi = {}
        parsed["personal_info"] = out_pi

    for key in (
        "spouse",
        "children",
        "siblings",
        "family_background",
        "estimated_age_band",
        "estimated_age_basis",
        "bachelors_year",
    ):
        if not out_pi.get(key) and pi.get(key):
            out_pi[key] = pi[key]

    family = parsed.get("family") if isinstance(parsed.get("family"), dict) else {}
    if not isinstance(family, dict):
        family = {}
    if not family.get("spouse") and (out_pi.get("spouse") or pi.get("spouse")):
        family["spouse"] = out_pi.get("spouse") or pi.get("spouse")
    if not family.get("children") and (out_pi.get("children") or pi.get("children")):
        family["children"] = out_pi.get("children") or pi.get("children")
    if not family.get("siblings") and (out_pi.get("siblings") or pi.get("siblings")):
        family["siblings"] = out_pi.get("siblings") or pi.get("siblings")
    if family:
        parsed["family"] = family

    if not parsed.get("estimated_age_band"):
        parsed["estimated_age_band"] = (
            out_pi.get("estimated_age_band") or pi.get("estimated_age_band")
        )
    if not parsed.get("estimated_age_basis"):
        parsed["estimated_age_basis"] = (
            out_pi.get("estimated_age_basis") or pi.get("estimated_age_basis")
        )
    return parsed


def _aggressive_compact(sources: dict) -> dict:
    """Extra-small payload for synthesize retry after MAX_TOKENS."""
    keep_keys = {
        "gemini_search",
        "exa_search",
        "personal_info",
        "linkedin_public",
        "deep_agent",
        "page_extracts",
        "nimble_pages",
        "enrichment",
        "apollo",
        "aleads",
        "enrichlayer",
        "public_web",
        "github",
    }
    out = {}
    for k, v in (sources or {}).items():
        if k not in keep_keys:
            continue
        out[k] = _compact_value(v, depth=0)
    # Cap strings harder
    blob = json.dumps(out, default=str)
    if len(blob) > 40000:
        return {"gemini_search": out.get("gemini_search"), "personal_info": out.get("personal_info"), "deep_agent": out.get("deep_agent")}
    return out



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
            if k in {"raw", "raw_text", "apidirect_posts", "candidates", "search_history", "html"}:
                continue
            if k == "markdown" and isinstance(v, str) and len(v) > 3500:
                out[k] = v[:3500] + "…"
                continue
            if k == "pages" and isinstance(v, list):
                # Keep Nimble page bodies for synthesize (capped)
                slim = []
                for p in v[:10]:
                    if not isinstance(p, dict):
                        continue
                    slim.append(
                        {
                            "url": p.get("final_url") or p.get("url"),
                            "title": p.get("title"),
                            "markdown": (p.get("markdown") or p.get("text") or "")[:3500],
                            "status": p.get("status"),
                            "extractor": p.get("extractor"),
                        }
                    )
                out[k] = slim
                continue
            if k == "evidence" and isinstance(v, list):
                out[k] = _cap_list(v, 24)
                continue
            if k == "search_trail" and isinstance(v, list):
                out[k] = _cap_list(v, 4)
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
