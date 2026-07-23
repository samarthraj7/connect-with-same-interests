"""Deep personal-info dig using expert biographical-researcher prompts.

Each factual milestone (birthplace, where raised, current location, hobbies,
sports/weekends, family) is queried with a strict multi-step sequence:
intent check → public-record retrieval → synthesis with explicit gap handling
(closest verified geographic/personal context when the exact fact is not public).
Never invents details.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"

# Milestone-specific researcher prompts. Each asks one strict factual question
# and requires verified public evidence — or an explicit "not public" + closest
# verified alternative (never a guess).
_MILESTONES = [
    {
        "key": "birthplace",
        "question": "Where was {name} born?",
        "milestone": "exact place of birth (city/town and country when stated)",
        "search_bias": (
            'born OR "born in" OR birthplace OR "place of birth" OR native OR '
            '"originally from" alumni directory biography interview'
        ),
        "fields": ["born_or_hometown"],
    },
    {
        "key": "raised",
        "question": "Where was {name} raised / where did they grow up?",
        "milestone": "where they grew up or were raised",
        "search_bias": (
            '"grew up" OR "raised in" OR hometown OR childhood OR "spent childhood" '
            "OR \"from\" alumni directory interview biography"
        ),
        "fields": ["raised_in"],
    },
    {
        "key": "current_location",
        "question": "Where does {name} live now / where are they based?",
        "milestone": "current city/region of residence or where they are based",
        "search_bias": (
            '"based in" OR "lives in" OR "living in" OR "resides in" OR '
            '"located in" executive bio company directory'
        ),
        "fields": ["current_location"],
    },
    {
        "key": "hobbies_lifestyle",
        "question": (
            "What hobbies, sports, or weekend activities does {name} publicly "
            "mention enjoying?"
        ),
        "milestone": "hobbies, sports interests, and weekend preferences",
        "search_bias": (
            'hobby OR hobbies OR "spare time" OR weekend OR "outside of work" OR '
            "sports OR soccer OR cricket OR tennis OR golf OR running OR hiking OR "
            "music OR cooking OR travel interview podcast bio"
        ),
        "fields": ["hobbies", "sports_interests", "weekend_preferences"],
    },
    {
        "key": "spouse",
        "question": "Is {name}'s spouse or partner publicly named (wedding announcements, bios)?",
        "milestone": "spouse/partner name only if publicly documented",
        "search_bias": (
            'married OR wedding OR spouse OR wife OR husband OR partner OR '
            '"and his wife" OR "and her husband" announcement biography'
        ),
        "fields": ["spouse", "family_background"],
    },
    {
        "key": "children",
        "question": (
            "Are {name}'s children (son/daughter) publicly named, and if so their "
            "schools or companies?"
        ),
        "milestone": "children names, schools, employers only if public",
        "search_bias": (
            'son OR daughter OR children OR child OR "his son" OR "her daughter" OR '
            '"and son" OR "and daughter" wedding obituary biography alumni'
        ),
        "fields": ["children", "family_background"],
    },
    {
        "key": "siblings",
        "question": "Are siblings of {name} publicly documented?",
        "milestone": "siblings only if public records/bios name them",
        "search_bias": (
            'sibling OR brother OR sister OR "twin" OR "brothers" OR "sisters" '
            "family biography obituary wedding"
        ),
        "fields": ["siblings", "family_background"],
    },
    {
        "key": "family_background",
        "question": "What broader family background about {name} is publicly documented?",
        "milestone": "parents / family background only if public",
        "search_bias": (
            'family OR parents OR "son of" OR "daughter of" OR "father" OR "mother" '
            "biography interview"
        ),
        "fields": ["family_background"],
    },
    {
        "key": "education_age",
        "question": (
            "When did {name} complete a bachelor's / undergraduate degree "
            "(year), if publicly stated?"
        ),
        "milestone": "bachelor's graduation year for age-band estimate only",
        "search_bias": (
            'bachelor OR "B.S." OR "B.A." OR "BTech" OR undergraduate OR graduated OR '
            'class of OR "alumni" year university commencement'
        ),
        "fields": ["bachelors_year", "estimated_age_band", "estimated_age_basis"],
    },
]

_RESEARCHER_PROMPT = """Act as an expert biographical researcher. Analyze this request:
"{question}"

Execute using this multi-step sequence:

1. INTENT & CONSTRAINT CHECK
Target subject: {name}
Known context (may help disambiguate — do NOT treat as answers):
{context}
Specific factual milestone requested: {milestone}
Treat this as a strict factual query requiring verified biographical data.
Do not guess or extrapolate.
IDENTITY LOCK: Do not mix up same-name people. Re-verify every fact against the
canonical LinkedIn / chosen identity in the context before keeping it. Maintain one
unique identity for the person chosen at the start. Every kept claim needs a public
source URL in evidence.source_hint. Prefer blogs, podcasts, alumni pages, wedding/
obituary notices, and personal sites over unsourced search snippets. Never invent family.

2. INFORMATION RETRIEVAL
Search verified public records and open web sources: alumni directories, university
profiles, professional/executive bios, company directories, LinkedIn public headline/
about text only if indexed in search results, reliable media, interviews, personal
blogs, speaker bios, local news.
Bias search toward: {search_bias}
Do not use login-gated social feeds as a source.

3. DATA SYNTHESIS & GAP HANDLING
- If the exact milestone fact is found and verified in a public source, put it in
  "direct_answer" and set "exact_found" to true.
- If the exact fact is NOT publicly documented, set "exact_found" to false,
  set "direct_answer" to a clear statement that it is not public knowledge,
  and fill "closest_verified_context" with the closest verified geographic or
  personal context available (e.g. where they grew up, university attended,
  early-career city, current base). Never hallucinate a location or fact.
- Populate structured fields only with verified values; use null / [] otherwise.
- Add short evidence entries (fact + source_hint) for every claim you keep.

4. OUTPUT
Respond with strict JSON only, no markdown fences:
{{
  "exact_found": boolean,
  "direct_answer": string,
  "closest_verified_context": string or null,
  "born_or_hometown": string or null,
  "raised_in": string or null,
  "current_location": string or null,
  "lived_in": [string],
  "hobbies": [string],
  "sports_interests": [string],
  "weekend_preferences": [string],
  "family_background": [string],
  "spouse": string or null,
  "children": [{{"name": string or null, "school": string or null, "company": string or null, "note": string or null}}],
  "siblings": [{{"name": string or null, "note": string or null}}],
  "bachelors_year": number or null,
  "estimated_age_band": string or null,
  "estimated_age_basis": string or null,
  "personal_notes": [string],
  "evidence": [{{"fact": string, "source_hint": string or null}}]
}}
"""


def search_personal_info(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    context = _context_line(name, company, university, place, linkedin_url=linkedin_url)
    print(f"  [personal_info] biographical research dig for {name!r}")

    client = genai.Client(api_key=api_key)
    max_workers = max(1, min(int(os.environ.get("PERSONAL_INFO_WORKERS") or "3"), len(_MILESTONES)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            m["key"]: pool.submit(_run_milestone, client, name, context, m)
            for m in _MILESTONES
        }
        milestone_results = {key: fut.result() for key, fut in futures.items()}

    return _merge(milestone_results)


def _context_line(
    name: str,
    company: Optional[str],
    university: Optional[str],
    place: Optional[str],
    linkedin_url: Optional[str] = None,
) -> str:
    from identity_lock import identity_lock_text, normalize_linkedin_url

    bits = [identity_lock_text(name=name, linkedin_url=linkedin_url, company=company, university=university)]
    bits.append(f'- Name: "{name}"')
    li = normalize_linkedin_url(linkedin_url)
    if li:
        bits.append(f'- Canonical LinkedIn: {li}')
    if company:
        bits.append(f'- Company/org: "{company}"')
    if university:
        bits.append(f'- University/school: "{university}"')
    if place:
        bits.append(f'- Place hint: "{place}"')
    if len(bits) <= 2 and not li and not company and not university:
        bits.append("- (no extra disambiguation hints)")
    return "\n".join(bits)


def _run_milestone(client, name: str, context: str, milestone: dict) -> dict:
    key = milestone["key"]
    question = milestone["question"].format(name=name)
    print(f"  [personal_info] milestone={key}: {question}")

    prompt = _RESEARCHER_PROMPT.format(
        question=question,
        name=name,
        context=context,
        milestone=milestone["milestone"],
        search_bias=milestone["search_bias"],
    )
    try:
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
    except (errors.ClientError, errors.ServerError) as exc:
        return {"status": "error", "milestone": key, "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "milestone": key, "error": str(exc)}

    parsed = _extract_json(response.text or "")
    if parsed is None:
        return {
            "status": "error",
            "milestone": key,
            "error": "could not parse biographical research response as JSON",
            "raw_text": (response.text or "")[:500],
        }

    has_signal = bool(
        parsed.get("exact_found")
        or parsed.get("born_or_hometown")
        or parsed.get("raised_in")
        or parsed.get("current_location")
        or parsed.get("hobbies")
        or parsed.get("sports_interests")
        or parsed.get("weekend_preferences")
        or parsed.get("family_background")
        or parsed.get("spouse")
        or parsed.get("children")
        or parsed.get("siblings")
        or parsed.get("bachelors_year")
        or parsed.get("closest_verified_context")
        or parsed.get("evidence")
    )
    parsed["status"] = "ok" if has_signal else "not_found"
    parsed["milestone"] = key
    parsed["question"] = question
    parsed["_source_urls"] = _grounding_urls(response)
    return parsed


def _merge(milestone_results: dict) -> dict:
    ordered = [milestone_results[m["key"]] for m in _MILESTONES if m["key"] in milestone_results]

    answers = {}
    for m in _MILESTONES:
        r = milestone_results.get(m["key"]) or {}
        answers[m["key"]] = {
            "question": r.get("question") or m["question"],
            "exact_found": bool(r.get("exact_found")),
            "direct_answer": r.get("direct_answer"),
            "closest_verified_context": r.get("closest_verified_context"),
            "status": r.get("status"),
        }

    merged = {
        "born_or_hometown": _first_non_null(ordered, "born_or_hometown"),
        "raised_in": _first_non_null(ordered, "raised_in"),
        "current_location": _first_non_null(ordered, "current_location"),
        "lived_in": _dedupe_list(r.get("lived_in") for r in ordered),
        "hobbies": _dedupe_list(r.get("hobbies") for r in ordered),
        "sports_interests": _dedupe_list(r.get("sports_interests") for r in ordered),
        "weekend_preferences": _dedupe_list(r.get("weekend_preferences") for r in ordered),
        "family_background": _dedupe_list(r.get("family_background") for r in ordered),
        "spouse": _first_non_null(ordered, "spouse"),
        "children": _dedupe_named(r.get("children") for r in ordered),
        "siblings": _dedupe_named(r.get("siblings") for r in ordered),
        "bachelors_year": _first_non_null(ordered, "bachelors_year"),
        "estimated_age_band": _first_non_null(ordered, "estimated_age_band"),
        "estimated_age_basis": _first_non_null(ordered, "estimated_age_basis"),
        "personal_notes": _dedupe_list(r.get("personal_notes") for r in ordered),
        "evidence": _dedupe_evidence(r.get("evidence") for r in ordered),
        "milestone_answers": answers,
        "search_angles": [
            {
                "milestone": r.get("milestone"),
                "status": r.get("status"),
                "exact_found": bool(r.get("exact_found")),
            }
            for r in ordered
        ],
        "sources": [
            {"url": url, "title": title}
            for url, title in _dedupe_urls(r.get("_source_urls") for r in ordered)
        ],
    }

    # Bridge birthplace gap: if exact birth city unknown, surface closest verified geo context
    birth = answers.get("birthplace") or {}
    if not merged["born_or_hometown"]:
        if birth.get("closest_verified_context"):
            merged["birthplace_note"] = (
                "Exact birth city is not public knowledge. "
                f"Closest verified geographic context: {birth['closest_verified_context']}"
            )
        elif birth.get("direct_answer"):
            merged["birthplace_note"] = birth["direct_answer"]

    # Age band from bachelor's year (~21–24 at graduation) — estimate only, never DOB
    by = merged.get("bachelors_year")
    try:
        by_int = int(by) if by is not None else None
    except (TypeError, ValueError):
        by_int = None
    if by_int and 1950 <= by_int <= 2035 and not merged.get("estimated_age_band"):
        from datetime import datetime

        year_now = datetime.now().year
        # Assume bachelor's typically finished by ~22–24
        age_lo = year_now - (by_int - 21)
        age_hi = year_now - (by_int - 24)
        if age_lo > age_hi:
            age_lo, age_hi = age_hi, age_lo
        mid = (age_lo + age_hi) // 2
        decade = (mid // 10) * 10
        merged["estimated_age_band"] = f"likely {decade}s (approx {age_lo}–{age_hi})"
        merged["estimated_age_basis"] = (
            f"Bachelor's/undergraduate year {by_int} publicly cited; "
            "assuming typical completion around age 21–24"
        )
        merged["bachelors_year"] = by_int

    has_any = any(
        [
            merged["born_or_hometown"],
            merged["raised_in"],
            merged["current_location"],
            merged["lived_in"],
            merged["hobbies"],
            merged["sports_interests"],
            merged["weekend_preferences"],
            merged["family_background"],
            merged.get("spouse"),
            merged.get("children"),
            merged.get("siblings"),
            merged.get("estimated_age_band"),
            merged["personal_notes"],
            merged.get("birthplace_note"),
            any(a.get("exact_found") or a.get("closest_verified_context") for a in answers.values()),
        ]
    )
    settled = any(r.get("status") in ("ok", "not_found") for r in ordered)
    if has_any:
        merged["status"] = "ok"
        merged["found"] = True
    elif settled:
        merged["status"] = "not_found"
        merged["found"] = False
    else:
        merged["status"] = "error"
        merged["found"] = False
        merged["error"] = "; ".join(r["error"] for r in ordered if r.get("error"))

    # Drop family claims that lack any source URL (never print raw Google guesses)
    merged = _reject_unsourced_family_fields(merged)
    # Second-hop: corroborate named relatives via blogs/LI search when present
    try:
        merged = _second_hop_family_verify(merged, ordered)
    except Exception as exc:
        print(f"  [personal_info] second-hop family skip: {exc}", flush=True)
    return merged


def _reject_unsourced_family_fields(merged: dict) -> dict:
    evidence_urls = [
        (e.get("source_hint") or "")
        for e in (merged.get("evidence") or [])
        if isinstance(e, dict)
    ]
    evidence_urls += [s.get("url") or "" for s in (merged.get("sources") or []) if isinstance(s, dict)]
    has_url = any(u.startswith("http") for u in evidence_urls)
    if not has_url:
        for key in ("spouse", "children", "siblings", "family_background", "estimated_age_band"):
            if key in merged and merged.get(key):
                print(f"  [personal_info] drop unsourced {key}", flush=True)
                merged[key] = [] if isinstance(merged.get(key), list) else None
                if key == "estimated_age_band":
                    merged["estimated_age_basis"] = None
    return merged


def _second_hop_family_verify(merged: dict, ordered: list) -> dict:
    """Light Exa/Gemini follow-up for named relatives — store relative_profiles when found."""
    relatives = []
    for child in merged.get("children") or []:
        if isinstance(child, dict) and child.get("name"):
            relatives.append({"relation": "child", "name": child["name"], "hint": child})
    for sib in merged.get("siblings") or []:
        if isinstance(sib, dict) and sib.get("name"):
            relatives.append({"relation": "sibling", "name": sib["name"], "hint": sib})
    if merged.get("spouse"):
        relatives.append({"relation": "spouse", "name": str(merged["spouse"]), "hint": None})
    if not relatives:
        return merged

    profiles = []
    try:
        from connectors import exa_search
    except Exception:
        return merged

    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return merged
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    for rel in relatives[:4]:
        q = f'"{rel["name"]}" LinkedIn OR biography OR podcast OR blog'
        try:
            results = exa_search._run_search(headers, q, num_results=3, want_text=True, exclude_domains=None)
        except Exception:
            results = []
        li = None
        urls = []
        for r in results or []:
            u = (r.get("url") or "").strip()
            if not u:
                continue
            urls.append(u)
            if "linkedin.com/in/" in u.lower() and not li:
                li = u
        if urls:
            profiles.append(
                {
                    "relation": rel["relation"],
                    "name": rel["name"],
                    "linkedin_url": li,
                    "source_urls": urls[:3],
                    "verified": bool(li) or len(urls) >= 2,
                }
            )
    if profiles:
        merged["relative_profiles"] = profiles
        print(f"  [personal_info] second-hop relatives: {len(profiles)}", flush=True)
    return merged


def _first_non_null(ordered: list, field: str):
    return next((r.get(field) for r in ordered if r.get(field)), None)


def _dedupe_list(list_of_lists) -> list:
    seen = set()
    out = []
    for lst in list_of_lists:
        for item in lst or []:
            if not isinstance(item, str):
                continue
            key = item.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(item.strip())
    return out


def _dedupe_named(list_of_lists) -> list:
    seen = set()
    out = []
    for lst in list_of_lists:
        for item in lst or []:
            if isinstance(item, str):
                item = {"name": item, "note": None}
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            key = name.lower() or json.dumps(item, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out[:12]


def _dedupe_evidence(list_of_lists) -> list:
    seen = set()
    out = []
    for lst in list_of_lists:
        for item in lst or []:
            if not isinstance(item, dict):
                continue
            fact = (item.get("fact") or "").strip()
            if not fact:
                continue
            key = fact.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"fact": fact, "source_hint": item.get("source_hint")})
    return out[:25]


def _dedupe_urls(list_of_lists) -> List[Tuple[str, str]]:
    seen = set()
    out = []
    for lst in list_of_lists:
        for url, title in lst or []:
            if url and url not in seen:
                seen.add(url)
                out.append((url, title))
    return out[:15]


def _extract_json(text: str) -> Optional[dict]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _grounding_urls(response) -> List[Tuple[str, str]]:
    urls = []
    try:
        for candidate in response.candidates or []:
            metadata = getattr(candidate, "grounding_metadata", None)
            if not metadata or not metadata.grounding_chunks:
                continue
            for chunk in metadata.grounding_chunks:
                web = getattr(chunk, "web", None)
                if web and web.uri:
                    urls.append((web.uri, web.title or ""))
    except AttributeError:
        pass
    return urls
