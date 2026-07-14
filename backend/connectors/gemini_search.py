import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from google import genai
from google.genai import errors, types

from connectors.opengraph import fetch_open_graph
from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
VERIFIED_PAGE_LIMIT = 8

CANDIDATE_PROMPT_TEMPLATE = """Search the public web — including LinkedIn search results, \
company directories, news, and conference/speaker pages — for people named "{name}". Real-world \
names are often shared by several different people; your job is to help tell them apart.

List every distinct individual you can find real evidence for, up to 8. For each one, report \
whatever of the following you can actually confirm from search results: current company, \
current role/title, general location, their LinkedIn profile URL if one appears in results \
(a link only — you cannot see what's posted behind it), and a publicly reachable profile photo \
URL if search results or page thumbnails include one (LinkedIn CDN media.licdn.com, company \
headshots, speaker pages, etc.). Do not invent photo URLs. Do not merge different people into one \
entry. Do not invent information — if you can only confirm a name and nothing else, list that \
entry with the other fields null.

Respond with strict JSON only, no markdown fences, matching this schema exactly:
{{
  "candidates": [
    {{"name": string, "company": string or null, "role": string or null, "location": string or null, "linkedin_url": string or null, "photo_url": string or null, "context": string}}
  ]
}}
"""

# Order matters: more targeted angles are trusted first when merging conflicting fields.
_ANGLE_PRIORITY = ["company", "leadership", "university", "place", "posts", "general"]

_SHARED_INSTRUCTIONS = """Only report facts you can actually find in search results — never \
guess or fill gaps with assumptions.

Do not rely on login-gated social media content as a source of facts — LinkedIn's post/activity \
feed and Instagram/Facebook profiles are not accessible without login and must not be used as a \
source, even if you're tempted to guess what they might contain.

Report their career history as a timeline if multiple past roles/companies are mentioned across \
sources, not just their current role.

Also report their LinkedIn, Instagram, Facebook, and Twitter/X profile URLs if a search result surfaces
one — just the link, even though you can't see what's actually posted behind it.

Also note any co-founders, executive team members, board members, or other named colleagues
that search results explicitly connect to this person (e.g. "co-founded the company with Jane
Doe, now CTO" or "joins the board alongside..."). Only include people who are explicitly named
in that context — do not guess or infer who this person might know. This is not their LinkedIn
connections list (that data isn't available here) — it's only what's been publicly reported.

Especially flag senior / C-level people publicly tied to them or their employer (CEO, CTO, CFO,
COO, CPO, VP, Director, board members) when search results name both people together or name
leadership of the same company in coverage about this person. Put those in senior_colleagues
with title when known; still put broader named colleagues in notable_colleagues.

Also note any awards, honors, or public recognitions explicitly reported in search results.

Also note public writing or posts BY this person that appear in open web results — blog posts,
conference talks, interviews they gave, newsletter issues, public X/Twitter threads indexed by
search, Medium/Substack articles, etc. Summarize what each is about. Do NOT invent LinkedIn
activity or "liked" posts — LinkedIn's feed and reactions are login-gated and unavailable here.
Only include liked_or_engaged_with if a public source explicitly shows them endorsing, quoting,
or reacting to something (rare).

Respond with strict JSON only, no markdown fences, matching this schema exactly:
{{
  "found": boolean,
  "current_role": string or null,
  "current_company": string or null,
  "career_history": [string],
  "education": [string],
  "bio_summary": string,
  "awards_and_recognitions": [string],
  "notable_colleagues": [{{"name": string, "context": string}}],
  "senior_colleagues": [{{"name": string, "title": string or null, "context": string, "seniority": "C-level" or "VP" or "Director" or "Board" or "other"}}],
  "public_posts_or_writing": [{{"topic": string, "source": string or null, "snippet": string or null}}],
  "liked_or_engaged_with": [{{"topic": string, "evidence": string}}],
  "social_profile_links": {{"linkedin": string or null, "instagram": string or null, "facebook": string or null, "twitter": string or null}}
}}
"""

_ANGLE_FOCUS = {
    "company": "Prioritize their employer's official team/people directory page, press coverage "
    "naming both the person and the company, leadership/exec pages for that employer, and "
    "professional profile pages tied to this employer.",
    "university": "Prioritize a university faculty, alumni, or student directory page, academic "
    "profile pages, and press coverage connecting them to this university.",
    "place": "Prioritize local news coverage, regional business or community directories, and "
    "event/organization pages based in or near this location.",
    "general": "Search broadly — personal website or portfolio, conference talks, podcast "
    "appearances, interviews, blog/newsletter posts they authored, awards pages, and any other "
    "public professional pages.",
    "posts": "Prioritize content this person authored or was quoted in — blog posts, talks, "
    "interviews, newsletters, public social posts indexed by search. Summarize topics.",
    "leadership": "Prioritize company leadership pages, exec team bios, and press that names "
    "CEO/CTO/CFO/COO/VP-level people at their employer or co-founders/board members tied to them.",
}


def find_candidates(name: str) -> dict:
    """A cheap, single search used to disambiguate a name before committing
    to the expensive multi-angle deep dive — surfaces the distinct people
    who share this name, with company/role/location/LinkedIn-link per
    candidate, entirely from web search (no LinkedIn login, no scraping)."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    print(f'  querying: web/LinkedIn/company-directory search for people named "{name}"')
    client = genai.Client(api_key=api_key)
    prompt = CANDIDATE_PROMPT_TEMPLATE.format(name=name)

    try:
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
    except (errors.ClientError, errors.ServerError) as exc:
        return {"status": "error", "error": str(exc)}

    parsed = _extract_json(response.text or "")
    if parsed is None:
        return {"status": "error", "error": "could not parse Gemini response as JSON", "raw_text": response.text}

    candidates = parsed.get("candidates") or []
    # Enrich missing photos via Apollo / public og:image (best-effort, parallel).
    candidates = _enrich_candidate_photos(candidates)
    return {"status": "ok" if candidates else "not_found", "candidates": candidates}


def _enrich_candidate_photos(candidates: list) -> list:
    """Fill photo_url when Gemini didn't find one — Apollo first, then OG image."""
    if not candidates:
        return candidates

    def enrich_one(c: dict) -> dict:
        if not isinstance(c, dict):
            return c
        out = dict(c)
        if out.get("photo_url"):
            return out
        name = (out.get("name") or "").strip()
        company = (out.get("company") or "").strip() or None
        linkedin = (out.get("linkedin_url") or "").strip() or None
        # 1) Apollo licensed photo when key present
        if name:
            try:
                from connectors import apollo

                hit = apollo.enrich_person(name=name, company=company, linkedin_url=linkedin)
                if hit.get("status") == "ok" and hit.get("photo_url"):
                    out["photo_url"] = hit["photo_url"]
                    out["photo_source"] = "apollo"
                    return out
            except Exception:
                pass
        # 2) Public page og:image (company bio / personal site / LinkedIn when exposed)
        for url in (linkedin,):
            if not url:
                continue
            og = fetch_open_graph(url)
            if og.get("status") == "ok" and og.get("image"):
                out["photo_url"] = og["image"]
                out["photo_source"] = "opengraph"
                return out
        return out

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(candidates)))) as pool:
        return list(pool.map(enrich_one, candidates))


def search_person(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
) -> dict:
    """Runs several targeted searches in parallel instead of one blended
    query — name+company, name+university, name+place (whichever hints are
    given), plus an always-on general angle — since each phrasing biases
    Google's results differently and widens what actually gets found. All
    results are then merged into one profile, with more targeted angles
    taking priority when fields conflict."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    angles = _build_angles(name, company, university, place)
    for angle, _, description in angles:
        print(f"  querying ({angle} angle): {description}")

    client = genai.Client(api_key=api_key)
    with ThreadPoolExecutor(max_workers=len(angles)) as pool:
        futures = {angle: pool.submit(_run_angle, client, prompt) for angle, prompt, _ in angles}
        angle_results = {angle: future.result() for angle, future in futures.items()}

    return _merge_angles(angle_results)


def _build_angles(name: str, company: Optional[str], university: Optional[str], place: Optional[str]) -> List[Tuple[str, str, str]]:
    angles = []
    if company:
        angles.append((
            "company",
            f"Person: {name}\nCompany: {company}\n\n{_ANGLE_FOCUS['company']}\n\n{_SHARED_INSTRUCTIONS}",
            f'"{name}" + company "{company}" — employer directory / press focus',
        ))
        angles.append((
            "leadership",
            f"Person: {name}\nCompany: {company}\n\n{_ANGLE_FOCUS['leadership']}\n\n{_SHARED_INSTRUCTIONS}",
            f'"{name}" + "{company}" leadership — CEO/CTO/CFO/VP colleagues',
        ))
    if university:
        angles.append((
            "university",
            f"Person: {name}\nUniversity: {university}\n\n{_ANGLE_FOCUS['university']}\n\n{_SHARED_INSTRUCTIONS}",
            f'"{name}" + university "{university}" — faculty/alumni directory focus',
        ))
    if place:
        angles.append((
            "place",
            f"Person: {name}\nLocation: {place}\n\n{_ANGLE_FOCUS['place']}\n\n{_SHARED_INSTRUCTIONS}",
            f'"{name}" + location "{place}" — local news/directory focus',
        ))
    angles.append((
        "posts",
        f"Person: {name}\nCompany: {company or '(unknown)'}\n\n{_ANGLE_FOCUS['posts']}\n\n{_SHARED_INSTRUCTIONS}",
        f'"{name}" — public posts / writing / talks they authored',
    ))
    angles.append((
        "general",
        f"Person: {name}\n\n{_ANGLE_FOCUS['general']}\n\n{_SHARED_INSTRUCTIONS}",
        f'"{name}" — broad web search (personal site, talks, press, awards)',
    ))
    return angles


def _run_angle(client, prompt: str) -> dict:
    """Runs one angle's search. Failures are contained here so one bad angle
    (rate limit, transient error) doesn't sink the other angles."""
    try:
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
    except (errors.ClientError, errors.ServerError) as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:  # the SDK raises several distinct exception types across failure modes
        return {"status": "error", "error": str(exc)}

    parsed = _extract_json(response.text or "")
    if parsed is None:
        return {"status": "error", "error": "could not parse Gemini response as JSON", "raw_text": response.text}

    parsed["status"] = "ok" if parsed.get("found") else "not_found"
    parsed["_source_urls"] = _grounding_urls(response)
    return parsed


def _merge_angles(angle_results: dict) -> dict:
    ordered = [angle_results[a] for a in _ANGLE_PRIORITY if a in angle_results]

    merged = {
        "found": any(r.get("found") for r in ordered),
        "current_role": _first_non_null(ordered, "current_role"),
        "current_company": _first_non_null(ordered, "current_company"),
        "career_history": _dedupe_scalars(r.get("career_history", []) for r in ordered),
        "education": _dedupe_scalars(r.get("education", []) for r in ordered),
        "bio_summary": next((r.get("bio_summary") for r in ordered if r.get("bio_summary")), ""),
        "awards_and_recognitions": _dedupe_scalars(r.get("awards_and_recognitions", []) for r in ordered),
        "notable_colleagues": _dedupe_named(r.get("notable_colleagues", []) for r in ordered),
        "senior_colleagues": _dedupe_named(r.get("senior_colleagues", []) for r in ordered),
        "public_posts_or_writing": _dedupe_posts(r.get("public_posts_or_writing", []) for r in ordered),
        "liked_or_engaged_with": _dedupe_posts(r.get("liked_or_engaged_with", []) for r in ordered),
        "social_profile_links": _merge_social_links(r.get("social_profile_links", {}) for r in ordered),
        "search_angles": [
            {"angle": angle, "status": angle_results[angle].get("status"), "found": angle_results[angle].get("found", False)}
            for angle in _ANGLE_PRIORITY
            if angle in angle_results
        ],
    }

    all_urls = _dedupe_urls((r.get("_source_urls") or []) for r in ordered)
    merged["sources"] = [{"url": url, "title": title} for url, title in all_urls]
    merged["verified_pages"] = [fetch_open_graph(url) for url, _ in all_urls[:VERIFIED_PAGE_LIMIT]]

    any_angle_settled = any(r.get("status") in ("ok", "not_found") for r in ordered)
    if merged["found"]:
        merged["status"] = "ok"
    elif any_angle_settled:
        merged["status"] = "not_found"
    else:
        merged["status"] = "error"
        merged["error"] = "; ".join(r["error"] for r in ordered if r.get("error"))
    return merged


def _first_non_null(ordered: list, field: str):
    return next((r.get(field) for r in ordered if r.get(field)), None)


def _dedupe_scalars(list_of_lists) -> list:
    seen = set()
    result = []
    for lst in list_of_lists:
        for item in lst or []:
            key = item if isinstance(item, (str, tuple)) else json.dumps(item, sort_keys=True)
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _dedupe_urls(list_of_lists) -> list:
    seen = set()
    result = []
    for lst in list_of_lists:
        for url, title in lst or []:
            if url not in seen:
                seen.add(url)
                result.append((url, title))
    return result


def _dedupe_named(list_of_lists) -> list:
    seen = set()
    result = []
    for lst in list_of_lists:
        for item in lst or []:
            key = (item.get("name") or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _dedupe_posts(list_of_lists) -> list:
    seen = set()
    result = []
    for lst in list_of_lists:
        for item in lst or []:
            key = (item.get("topic") or item.get("snippet") or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _merge_social_links(list_of_dicts) -> dict:
    merged = {"linkedin": None, "instagram": None, "facebook": None, "twitter": None}
    for d in list_of_dicts:
        for platform in merged:
            if not merged[platform] and d and d.get(platform):
                merged[platform] = d[platform]
            # accept "x" as twitter alias
            if platform == "twitter" and not merged["twitter"] and d and d.get("x"):
                merged["twitter"] = d["x"]
    return merged


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
