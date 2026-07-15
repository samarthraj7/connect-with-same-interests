import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from google import genai
from google.genai import errors, types

from connectors.opengraph import fetch_open_graph
from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
VERIFIED_PAGE_LIMIT = 8

# Prose first — Gemini+Google Search often returns empty parts when asked for JSON-only.
CANDIDATE_SEARCH_PROMPT = """Use Google Search to find distinct real people named "{name}".
Write a numbered list of up to 8 individuals you have evidence for.
For each person include: name, company (or null), role/title, location, LinkedIn URL if any,
a publicly reachable profile PHOTO URL if any search result/thumbnail shows one
(LinkedIn media.licdn.com, company bio headshot, speaker page image, GitHub avatar, etc.),
and one-line context.
Do not invent people or photo URLs. If search finds nobody, reply with exactly: NO_MATCHES
"""

CANDIDATE_JSON_PROMPT = """Convert these research notes into JSON only (no markdown fences).
Schema:
{{"candidates":[{{"name":string,"company":string|null,"role":string|null,"location":string|null,"linkedin_url":string|null,"photo_url":string|null,"context":string}}]}}
Keep photo_url when notes include a real http(s) image URL; otherwise null.
If notes say NO_MATCHES or are empty, return {{"candidates":[]}}.
Notes:
{notes}
"""

# Kept for CLI / older callers that expect a single JSON prompt.
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

IDENTITY: Obey the IDENTITY LOCK block in the prompt if present. Never mix facts from a \
different person who shares this name. If a result is ambiguous, omit it.

Do not rely on login-gated social media content as a source of facts — LinkedIn's post/activity \
feed and Instagram/Facebook profiles are not accessible without login and must not be used as a \
source, even if you're tempted to guess what they might contain.

Report their career history as a timeline if multiple past roles/companies are mentioned across \
sources, not just their current role.

Also report their LinkedIn, Instagram, Facebook, and Twitter/X profile URLs if a search result surfaces
one — just the link, even though you can't see what's actually posted behind it. If a canonical \
LinkedIn URL is given in IDENTITY LOCK, set social_profile_links.linkedin to that exact URL.

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
search, Medium/Substack articles, etc. Summarize what each is about and include the source_url
when known. Do NOT invent LinkedIn activity or "liked" posts — LinkedIn's feed and reactions are \
login-gated and unavailable here. Only include liked_or_engaged_with if a public source explicitly \
shows them endorsing, quoting, or reacting to something (rare).

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
  "public_posts_or_writing": [{{"topic": string, "source": string or null, "source_url": string or null, "snippet": string or null}}],
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


def _response_text(response) -> str:
    """Pull text even when response.text is None (tool/search quirk)."""
    try:
        if response.text:
            return response.text
    except Exception:
        pass
    chunks = []
    try:
        for cand in response.candidates or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", None) or []:
                t = getattr(part, "text", None)
                if t:
                    chunks.append(t)
    except Exception as exc:
        print(f"[find_candidates] _response_text parts error: {exc}", flush=True)
    return "\n".join(chunks).strip()


def _grounded_search_notes(client, name: str) -> Tuple[str, object]:
    """Phase 1: Google Search → prose notes. Retries once if Gemini returns empty parts."""
    prompt = CANDIDATE_SEARCH_PROMPT.format(name=name)
    last_resp = None
    for attempt in range(2):
        print(f"[find_candidates] phase1 search attempt {attempt + 1}/2…", flush=True)
        last_resp = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
        text = _response_text(last_resp)
        grounding = _grounding_urls(last_resp)
        print(
            f"[find_candidates] phase1 text_len={len(text)} grounding_urls={len(grounding)}",
            flush=True,
        )
        if text:
            print(f"[find_candidates] phase1 preview: {text[:400].replace(chr(10), ' ')}…", flush=True)
            return text, last_resp
        # Empty STOP with 0 chunks is a Gemini+Search flake for rare names — brief pause + retry.
        time.sleep(0.6)
    return "", last_resp


def _notes_to_candidates_json(client, notes: str) -> Optional[dict]:
    """Phase 2: prose → JSON (no tools; mime JSON works)."""
    if not notes.strip() or notes.strip().upper().startswith("NO_MATCHES"):
        return {"candidates": []}
    print("[find_candidates] phase2 converting notes → JSON…", flush=True)
    response = generate_with_retry(
        client,
        model=MODEL,
        contents=CANDIDATE_JSON_PROMPT.format(notes=notes[:8000]),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    raw = _response_text(response)
    print(f"[find_candidates] phase2 json_len={len(raw)}", flush=True)
    parsed = _extract_json(raw)
    if parsed is None:
        print(f"[find_candidates] phase2 parse fail preview={raw[:300]!r}", flush=True)
    return parsed


def find_candidates(name: str, *, enrich_photos: bool = True) -> dict:
    """Disambiguate a name before the expensive multi-angle deep dive.

    Primary path (no company / university / LinkedIn required):
      Exa LinkedIn people web search → linkedin.com/in/… profiles
    Fallback: Gemini Google Search when Exa finds nobody.
    Photos: Enrich Layer (LinkedIn URL) → Apollo → GitHub/OG → monogram.
    """
    print(f"[find_candidates] START name={name!r} model={MODEL}", flush=True)
    warning = None
    candidates: list = []
    discovery = "none"

    # ── 1) LinkedIn people web search via Exa (name only) ──────────────────
    try:
        from connectors import exa_search

        print("[find_candidates] primary: Exa LinkedIn people search…", flush=True)
        exa = exa_search.find_linkedin_people_by_name(name)
        print(
            f"[find_candidates] exa status={exa.get('status')} "
            f"n={len(exa.get('candidates') or [])}",
            flush=True,
        )
        if exa.get("status") == "ok" and exa.get("candidates"):
            candidates = [c for c in exa["candidates"] if isinstance(c, dict)]
            discovery = "exa_linkedin_people"
    except Exception as exc:
        print(f"[find_candidates] exa exception: {exc}", flush=True)

    # ── 2) Gemini grounded web search if Exa empty / skipped ───────────────
    if not candidates:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("[find_candidates] no Exa hits and GEMINI_API_KEY not set", flush=True)
            if not os.environ.get("EXA_API_KEY"):
                return {
                    "status": "skipped",
                    "error": "EXA_API_KEY and GEMINI_API_KEY not set",
                    "candidates": [],
                }
        else:
            print("[find_candidates] fallback: Gemini Google Search…", flush=True)
            client = genai.Client(api_key=api_key)
            try:
                notes, _resp = _grounded_search_notes(client, name)
            except (errors.ClientError, errors.ServerError) as exc:
                print(f"[find_candidates] Gemini API ERROR: {exc}", flush=True)
                return {"status": "error", "error": str(exc), "candidates": []}
            except Exception as exc:
                print(f"[find_candidates] UNEXPECTED ERROR: {type(exc).__name__}: {exc}", flush=True)
                return {"status": "error", "error": str(exc), "candidates": []}

            if notes.strip():
                try:
                    parsed = _notes_to_candidates_json(client, notes)
                except Exception as exc:
                    print(f"[find_candidates] phase2 ERROR: {exc}", flush=True)
                    parsed = _extract_json(notes)
                if parsed is None:
                    return {
                        "status": "error",
                        "error": "could not parse Gemini response as JSON",
                        "raw_text": notes[:2000],
                        "candidates": [],
                    }
                candidates = [c for c in (parsed.get("candidates") or []) if isinstance(c, dict)]
                discovery = "gemini_search"

    if not candidates:
        print("[find_candidates] EMPTY — typed-name passthrough", flush=True)
        candidates = [
            {
                "name": name.strip(),
                "company": None,
                "role": None,
                "location": None,
                "linkedin_url": None,
                "photo_url": None,
                "context": "No LinkedIn people matches found for this name. Add company, university, or LinkedIn, or continue as typed.",
            }
        ]
        warning = "no_public_matches"
        discovery = "passthrough"

    print(f"[find_candidates] discovery={discovery} count={len(candidates)}", flush=True)
    for i, c in enumerate(candidates):
        print(
            f"  [{i}] name={c.get('name')!r} company={c.get('company')!r} "
            f"role={c.get('role')!r} linkedin={c.get('linkedin_url')!r}",
            flush=True,
        )

    skip_photos = os.environ.get("CANDIDATE_PHOTO_ENRICH", "").strip().lower() in ("0", "false", "no")
    want_photos = enrich_photos and not skip_photos
    if want_photos and candidates:
        print("[find_candidates] photo enrich ON (Enrich Layer → Apollo → …)…", flush=True)
        candidates = _enrich_candidate_photos(candidates)
        with_photo = sum(
            1
            for c in candidates
            if isinstance(c, dict)
            and (c.get("photo_url") or "").startswith("http")
            and "ui-avatars.com" not in (c.get("photo_url") or "")
        )
        print(f"[find_candidates] real photos filled: {with_photo}/{len(candidates)}", flush=True)
    else:
        print("[find_candidates] photo enrich SKIPPED", flush=True)

    print(f"[find_candidates] DONE status=ok count={len(candidates)}", flush=True)
    out = {"status": "ok", "candidates": candidates, "discovery": discovery}
    if warning:
        out["warning"] = warning
    return out


def _enrich_candidate_photos(candidates: list) -> list:
    """Fill photo_url — Enrich Layer (LinkedIn) → Apollo → GitHub → OG → hunt → monogram."""
    if not candidates:
        return candidates

    APOLLO_TIMEOUT = 8.0
    EL_TIMEOUT = 12.0
    work = list(candidates[:6])
    rest = list(candidates[6:])

    def enrich_one(c: dict) -> dict:
        if not isinstance(c, dict):
            return c
        out = dict(c)
        existing = (out.get("photo_url") or "").strip()
        if existing.startswith("http") and _looks_like_image_url(existing) and "ui-avatars.com" not in existing:
            print(f"  [photo] keep gemini photo for {out.get('name')!r}", flush=True)
            return out
        name = (out.get("name") or "").strip()
        company = (out.get("company") or "").strip() or None
        linkedin = (out.get("linkedin_url") or "").strip() or None
        role = (out.get("role") or "").strip() or None
        location = (out.get("location") or "").strip() or None
        print(f"  [photo] enrich {name!r} company={company!r} li={bool(linkedin)}", flush=True)

        # 1) Enrich Layer — LinkedIn photo (needs URL, or name+company to resolve)
        try:
            from connectors import enrichlayer

            if enrichlayer.configured():
                el = enrichlayer.enrich_photo_for_candidate(
                    name=name or None,
                    company=company,
                    linkedin_url=linkedin,
                    role=role,
                    location=location,
                    timeout=EL_TIMEOUT,
                )
                print(
                    f"  [photo] enrichlayer status={el.get('status')} photo={bool(el.get('photo_url'))}",
                    flush=True,
                )
                if el.get("linkedin_url") and not out.get("linkedin_url"):
                    out["linkedin_url"] = el["linkedin_url"]
                    linkedin = el["linkedin_url"]
                if el.get("status") == "ok" and el.get("photo_url"):
                    out["photo_url"] = el["photo_url"]
                    out["photo_source"] = "enrichlayer"
                    if el.get("title") and not out.get("role"):
                        out["role"] = el["title"]
                    if (el.get("organization") or {}).get("name") and not out.get("company"):
                        out["company"] = el["organization"]["name"]
                    return out
        except Exception as exc:
            print(f"  [photo] enrichlayer exception: {exc}", flush=True)

        # 2) GitHub handle in context → stable avatar
        gh = _github_handle_from_candidate(out)
        if gh:
            avatar = f"https://github.com/{gh}.png"
            print(f"  [photo] github handle {gh!r} → {avatar}", flush=True)
            if _url_reachable(avatar):
                out["photo_url"] = avatar
                out["photo_source"] = "github"
                return out

        # 3) Apollo — may fill LI + photo when company known
        if name:
            try:
                from connectors import apollo

                hit = apollo.enrich_person(
                    name=name,
                    company=company,
                    linkedin_url=linkedin or out.get("linkedin_url"),
                    timeout=APOLLO_TIMEOUT,
                )
                print(
                    f"  [photo] apollo status={hit.get('status')} photo={bool(hit.get('photo_url'))}",
                    flush=True,
                )
                if hit.get("status") == "ok":
                    if hit.get("photo_url"):
                        out["photo_url"] = hit["photo_url"]
                        out["photo_source"] = "apollo"
                    if not out.get("linkedin_url") and hit.get("linkedin_url"):
                        out["linkedin_url"] = hit["linkedin_url"]
                        linkedin = hit["linkedin_url"]
                    if not out.get("company") and (hit.get("organization") or {}).get("name"):
                        out["company"] = hit["organization"]["name"]
                    if not out.get("role") and hit.get("title"):
                        out["role"] = hit["title"]
                    if out.get("photo_url"):
                        return out
                    # Newly discovered LinkedIn → Enrich Layer photo (often better than OG)
                    if linkedin:
                        try:
                            from connectors import enrichlayer

                            if enrichlayer.configured():
                                el2 = enrichlayer.profile_picture(linkedin, timeout=EL_TIMEOUT)
                                if el2.get("status") == "ok" and el2.get("photo_url"):
                                    out["photo_url"] = el2["photo_url"]
                                    out["photo_source"] = "enrichlayer"
                                    return out
                        except Exception as exc:
                            print(f"  [photo] enrichlayer after apollo: {exc}", flush=True)
                    ghu = (hit.get("github_url") or "").strip()
                    m = re.search(r"github\.com/([^/\s?#]+)", ghu or "")
                    if m:
                        avatar = f"https://github.com/{m.group(1)}.png"
                        if _url_reachable(avatar):
                            out["photo_url"] = avatar
                            out["photo_source"] = "github"
                            return out
            except Exception as exc:
                print(f"  [photo] apollo exception: {exc}", flush=True)

        for url in (out.get("linkedin_url"), linkedin):
            if not url:
                continue
            print(f"  [photo] og fetch {url!r}", flush=True)
            og = fetch_open_graph(url)
            print(f"  [photo] og status={og.get('status')} image={bool(og.get('image'))}", flush=True)
            if og.get("status") == "ok" and og.get("image"):
                out["photo_url"] = og["image"]
                out["photo_source"] = "opengraph"
                return out
        print(f"  [photo] no photo yet for {name!r}", flush=True)
        return out

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(work)))) as pool:
        enriched = list(pool.map(enrich_one, work))

    still_missing = [
        c
        for c in enriched
        if isinstance(c, dict) and not (c.get("photo_url") or "").startswith("http")
    ]
    if still_missing:
        print(f"[find_candidates] photo hunt for {len(still_missing)} missing…", flush=True)
        hunted = _gemini_photo_hunt(still_missing)
        if hunted:
            for c in enriched:
                if not isinstance(c, dict):
                    continue
                if (c.get("photo_url") or "").startswith("http"):
                    continue
                key = (c.get("name") or "").strip().lower()
                url = hunted.get(key)
                if url and _url_reachable(url):
                    c["photo_url"] = url
                    c["photo_source"] = "gemini_photo_hunt"
                    print(f"  [photo] hunt hit {c.get('name')!r} → {url[:80]}", flush=True)

    for c in enriched:
        if not isinstance(c, dict):
            continue
        if (c.get("photo_url") or "").startswith("http"):
            continue
        monogram = _monogram_avatar_url(c.get("name") or "?")
        c["photo_url"] = monogram
        c["photo_source"] = "monogram"
        print(f"  [photo] monogram for {c.get('name')!r}", flush=True)

    return enriched + rest


def _looks_like_image_url(url: str) -> bool:
    u = url.lower()
    if any(x in u for x in (".png", ".jpg", ".jpeg", ".webp", ".gif", "avatar", "media.licdn", "googleusercontent")):
        return True
    return u.startswith("http")


def _github_handle_from_candidate(c: dict) -> Optional[str]:
    blob = " ".join(
        str(c.get(k) or "")
        for k in ("context", "role", "company", "linkedin_url", "name")
    )
    m = re.search(r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?)", blob, re.I)
    if m:
        return m.group(1)
    m = re.search(r"`([A-Za-z0-9-]{2,39})`", blob)
    if m and "github" in blob.lower():
        return m.group(1)
    m = re.search(
        r"(?:github\s+(?:user|handle|username)?\s*[:=]?\s*|GitHub user \(\s*|handle\s+)"
        r"[`'\"]?([A-Za-z0-9-]{2,39})",
        blob,
        re.I,
    )
    if m:
        return m.group(1)
    return None


def _url_reachable(url: str, timeout: float = 4.0, *, min_bytes: int = 2000) -> bool:
    """HEAD/GET check; reject tiny placeholders (e.g. unavatar fallback silhouettes)."""
    try:
        import requests

        r = requests.head(url, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            r = requests.get(url, timeout=timeout, stream=True)
        if r.status_code >= 400:
            print(f"  [photo] url check {url[:70]} → {r.status_code}", flush=True)
            return False
        cl = r.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) < min_bytes:
                    print(f"  [photo] url too small ({cl}b) {url[:70]}", flush=True)
                    return False
            except ValueError:
                pass
        print(f"  [photo] url check {url[:70]} → {r.status_code}", flush=True)
        return True
    except Exception as exc:
        print(f"  [photo] url check fail {url[:70]}: {exc}", flush=True)
        return False


def _monogram_avatar_url(name: str) -> str:
    """Public monogram CDN — better than a blank circle when no headshot exists."""
    from urllib.parse import quote

    n = (name or "?").strip() or "?"
    return (
        "https://ui-avatars.com/api/"
        f"?name={quote(n)}&size=128&background=1A2F28&color=fff&bold=true"
    )


def _gemini_photo_hunt(candidates: list) -> dict:
    """One grounded call: try to find public photo URLs for people still missing them."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not candidates:
        return {}
    lines = []
    for i, c in enumerate(candidates[:5]):
        lines.append(
            f"{i+1}. name={c.get('name')!r} company={c.get('company')!r} "
            f"role={c.get('role')!r} linkedin={c.get('linkedin_url')!r} "
            f"context={(c.get('context') or '')[:120]!r}"
        )
    prompt = (
        "Search the public web for a direct profile PHOTO URL for each person below. "
        "Prefer LinkedIn CDN (media.licdn.com), GitHub (github.com/USER.png or avatars.githubusercontent.com), "
        "company bio headshots, or speaker pages. Do not invent URLs.\n"
        + "\n".join(lines)
        + '\nRespond with JSON only: {"photos":[{"name":string,"photo_url":string|null}]}'
    )
    try:
        client = genai.Client(api_key=api_key)
        resp = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
        raw = _response_text(resp)
        print(f"[find_candidates] photo_hunt raw_len={len(raw)}", flush=True)
        if not raw:
            return {}
        parsed = _extract_json(raw)
        if not parsed:
            return {}
        out = {}
        for item in parsed.get("photos") or []:
            if not isinstance(item, dict):
                continue
            n = (item.get("name") or "").strip().lower()
            u = (item.get("photo_url") or "").strip()
            if n and u.startswith("http"):
                out[n] = u
        return out
    except Exception as exc:
        print(f"[find_candidates] photo_hunt error: {exc}", flush=True)
        return {}


def search_person(
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> dict:
    """Runs several targeted searches in parallel instead of one blended
    query — name+company, name+university, name+place (whichever hints are
    given), plus an always-on general angle — since each phrasing biases
    Google's results differently and widens what actually gets found. All
    results are then merged into one profile, with more targeted angles
    taking priority when fields conflict.

    When linkedin_url is set, every angle is identity-locked to that profile
    so same-name people are not mixed into the dossier.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "GEMINI_API_KEY not set"}

    angles = _build_angles(name, company, university, place, linkedin_url=linkedin_url)
    for angle, _, description in angles:
        print(f"  querying ({angle} angle): {description}")

    client = genai.Client(api_key=api_key)
    with ThreadPoolExecutor(max_workers=len(angles)) as pool:
        futures = {angle: pool.submit(_run_angle, client, prompt) for angle, prompt, _ in angles}
        angle_results = {angle: future.result() for angle, future in futures.items()}

    return _merge_angles(angle_results, canonical_linkedin=linkedin_url)


def _build_angles(
    name: str,
    company: Optional[str],
    university: Optional[str],
    place: Optional[str],
    linkedin_url: Optional[str] = None,
) -> List[Tuple[str, str, str]]:
    from identity_lock import identity_lock_text, normalize_linkedin_url

    lock = identity_lock_text(
        name=name,
        linkedin_url=linkedin_url,
        company=company,
        university=university,
    )
    li = normalize_linkedin_url(linkedin_url)

    def wrap(focus: str) -> str:
        return f"{lock}\n\nPerson: {name}\nCompany: {company or '(unknown)'}\nUniversity: {university or '(unknown)'}\nLinkedIn: {li or '(unknown)'}\n\n{focus}\n\n{_SHARED_INSTRUCTIONS}"

    angles = []
    if company:
        angles.append((
            "company",
            wrap(_ANGLE_FOCUS["company"]),
            f'"{name}" + company "{company}" — employer directory / press focus',
        ))
        angles.append((
            "leadership",
            wrap(_ANGLE_FOCUS["leadership"]),
            f'"{name}" + "{company}" leadership — CEO/CTO/CFO/VP colleagues',
        ))
    if university:
        angles.append((
            "university",
            wrap(_ANGLE_FOCUS["university"]),
            f'"{name}" + university "{university}" — faculty/alumni directory focus',
        ))
    if place:
        angles.append((
            "place",
            wrap(_ANGLE_FOCUS["place"]),
            f'"{name}" + location "{place}" — local news/directory focus',
        ))
    angles.append((
        "posts",
        wrap(_ANGLE_FOCUS["posts"]),
        f'"{name}" — public posts / writing / talks they authored',
    ))
    # When LinkedIn+company are known, still run a tight general angle but refuse bare-name sprawl
    general_focus = _ANGLE_FOCUS["general"]
    if li:
        general_focus += (
            f" Prioritize pages that reference this LinkedIn profile ({li}) or the same "
            f"employer/school. Skip biographies of other people named {name}."
        )
    angles.append((
        "general",
        wrap(general_focus),
        f'"{name}" — broad web search anchored to identity lock',
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


def _merge_angles(angle_results: dict, canonical_linkedin: Optional[str] = None) -> dict:
    from identity_lock import normalize_linkedin_url, same_linkedin

    canonical = normalize_linkedin_url(canonical_linkedin)
    filtered = {}
    for angle, result in angle_results.items():
        if not isinstance(result, dict):
            continue
        if result.get("status") == "error":
            filtered[angle] = result
            continue
        links = result.get("social_profile_links") or {}
        other_li = links.get("linkedin") if isinstance(links, dict) else None
        if canonical and other_li and not same_linkedin(canonical, other_li):
            print(
                f"  [identity] drop angle={angle} — LinkedIn mismatch "
                f"{other_li!r} vs canonical {canonical!r}",
                flush=True,
            )
            continue
        # Force canonical LinkedIn onto matching angles
        if canonical:
            links = dict(links) if isinstance(links, dict) else {}
            links["linkedin"] = canonical
            result = dict(result)
            result["social_profile_links"] = links
        filtered[angle] = result

    angle_results = filtered or angle_results
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
        "canonical_linkedin_url": canonical,
    }

    if canonical:
        links = merged.get("social_profile_links") or {}
        links["linkedin"] = canonical
        merged["social_profile_links"] = links

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
    if not text:
        return None
    cleaned = text.strip()
    # Strip ```json ... ``` fences if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    blob = cleaned[start : end + 1]
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Trailing commas / light cleanup
        soft = re.sub(r",\s*([}\]])", r"\1", blob)
        try:
            return json.loads(soft)
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
