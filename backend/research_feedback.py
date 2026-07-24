"""User ratings on research quality + correction notes for next run."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from identity_lock import normalize_linkedin_url

_ROOT = Path(__file__).resolve().parent / "profiles" / "_feedback"


def _dir() -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    return _ROOT


def _index_path() -> Path:
    return _dir() / "index.json"


def _load_index() -> list:
    p = _index_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _save_index(rows: list) -> None:
    _index_path().write_text(json.dumps(rows, indent=2))


def record_feedback(
    *,
    user_id: Optional[str],
    rating: str,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    person_slug: Optional[str] = None,
    draft_id: Optional[str] = None,
    wrong_notes: Optional[str] = None,
    wrong_categories: Optional[List[str]] = None,
    briefing_snapshot: Optional[dict] = None,
) -> dict[str, Any]:
    rating = (rating or "").strip().lower()
    if rating not in ("good", "bad"):
        raise ValueError("rating must be good or bad")

    row = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "rating": rating,
        "name": (name or "").strip(),
        "company": (company or "").strip() or None,
        "university": (university or "").strip() or None,
        "linkedin_url": normalize_linkedin_url(linkedin_url),
        "person_slug": person_slug,
        "draft_id": draft_id,
        "wrong_notes": (wrong_notes or "").strip() or None,
        "wrong_categories": wrong_categories or [],
        "briefing_snapshot": briefing_snapshot,
        "applied_on_next_research": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    rows = _load_index()
    rows.insert(0, row)
    _save_index(rows[:500])  # keep last 500
    _dual_write_supabase(row)
    print(
        f"  [feedback] {rating} for {row['name']!r} li={row['linkedin_url']!r} "
        f"notes={bool(row['wrong_notes'])}",
        flush=True,
    )
    return row


def prior_bad_corrections(
    *,
    name: str,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    limit: int = 5,
) -> List[dict[str, Any]]:
    """Open (or recent) bad feedback for this identity — inject into synthesize."""
    li = normalize_linkedin_url(linkedin_url)
    name_l = (name or "").strip().lower()
    co_l = (company or "").strip().lower()
    out = []
    for row in _load_index():
        if row.get("rating") != "bad":
            continue
        row_li = normalize_linkedin_url(row.get("linkedin_url"))
        if li and row_li and li == row_li:
            out.append(row)
        elif name_l and (row.get("name") or "").strip().lower() == name_l:
            row_co = (row.get("company") or "").strip().lower()
            if not co_l or not row_co or co_l == row_co:
                out.append(row)
        if len(out) >= limit:
            break
    return out


def has_blocking_bad_feedback(
    *,
    name: str,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> bool:
    """If user marked research bad and hasn't been superseded by a good rating."""
    li = normalize_linkedin_url(linkedin_url)
    name_l = (name or "").strip().lower()
    co_l = (company or "").strip().lower()
    for row in _load_index():
        row_li = normalize_linkedin_url(row.get("linkedin_url"))
        match = False
        if li and row_li and li == row_li:
            match = True
        elif name_l and (row.get("name") or "").strip().lower() == name_l:
            row_co = (row.get("company") or "").strip().lower()
            if not co_l or not row_co or co_l == row_co:
                match = True
        if not match:
            continue
        if row.get("rating") == "good":
            return False  # newer good supersedes (index is newest-first)
        if row.get("rating") == "bad":
            return True
    return False


def mark_applied(feedback_ids: List[str]) -> None:
    if not feedback_ids:
        return
    want = set(feedback_ids)
    rows = _load_index()
    changed = False
    for row in rows:
        if row.get("id") in want and not row.get("applied_on_next_research"):
            row["applied_on_next_research"] = True
            changed = True
    if changed:
        _save_index(rows)


def corrections_prompt_block(rows: List[dict[str, Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "PRIOR USER CORRECTIONS for this identity (MUST honor — prefer omit over repeating mistakes):"
    ]
    for r in rows:
        cats = ", ".join(r.get("wrong_categories") or []) or "unspecified"
        notes = r.get("wrong_notes") or "(no freeform notes)"
        lines.append(f"- categories=[{cats}]; user said: {notes}")
    lines.append(
        "Do not re-introduce facts the user flagged as wrong. If unsure, leave fields empty "
        "rather than risk mixing another same-name person."
    )
    return "\n".join(lines)


def search_constraints_from_feedback(rows: List[dict[str, Any]]) -> dict[str, Any]:
    """Parse bad-feedback notes into query constraints for Gemini/Exa/social retries.

    Returns keys used by connectors:
      reject_slugs, reject_handles, reject_domains, exclude_phrases,
      prefer_company, prefer_university, query_suffix, prompt_block
    """
    import re

    reject_slugs: list[str] = []
    reject_handles: list[str] = []
    reject_domains: list[str] = []
    exclude_phrases: list[str] = []
    prefer_company: Optional[str] = None
    prefer_university: Optional[str] = None
    extra_bits: list[str] = []

    for r in rows or []:
        notes = (r.get("wrong_notes") or "").strip()
        cats = [c.lower() for c in (r.get("wrong_categories") or [])]
        blob = f"{notes} {' '.join(cats)}".lower()

        # LinkedIn /in/slug or bare slug mentions
        for m in re.finditer(
            r"(?:linkedin\.com/in/|wrong\s+(?:person|profile|linkedin)\s+[:\-]?\s*)([a-z0-9\-_%]{2,80})",
            notes,
            re.I,
        ):
            slug = m.group(1).strip("/").lower()
            if slug and slug not in reject_slugs and "http" not in slug:
                reject_slugs.append(slug)

        for m in re.finditer(r"@([A-Za-z0-9._]{2,40})", notes):
            h = m.group(1).lower()
            if h not in reject_handles:
                reject_handles.append(h)

        # "not at X" / "wrong company X" / "works at Y instead"
        m = re.search(
            r"(?:not\s+(?:at|with)|wrong\s+company|isn't\s+at|isn't\s+with)\s+([A-Za-z0-9][\w .&'-]{1,40})",
            notes,
            re.I,
        )
        if m:
            phrase = m.group(1).strip(" .,;")
            if phrase and phrase.lower() not in {p.lower() for p in exclude_phrases}:
                exclude_phrases.append(phrase)

        m = re.search(
            r"(?:actually\s+(?:at|with)|correct\s+company|works?\s+at|is\s+at)\s+([A-Za-z0-9][\w .&'-]{1,40})",
            notes,
            re.I,
        )
        if m and not prefer_company:
            prefer_company = m.group(1).strip(" .,;")

        m = re.search(
            r"(?:wrong\s+(?:school|university|college)|not\s+(?:from|at))\s+([A-Za-z0-9][\w .&'-]{1,50})",
            notes,
            re.I,
        )
        if m:
            phrase = m.group(1).strip(" .,;")
            if phrase and phrase.lower() not in {p.lower() for p in exclude_phrases}:
                exclude_phrases.append(phrase)

        m = re.search(
            r"(?:actually\s+(?:at|from)|correct\s+(?:school|university)|went\s+to)\s+([A-Za-z0-9][\w .&'-]{1,50})",
            notes,
            re.I,
        )
        if m and not prefer_university:
            prefer_university = m.group(1).strip(" .,;")

        if "wrong person" in blob or "wrong profile" in blob or "different person" in blob:
            extra_bits.append("exclude other same-name people")
        if "outdated" in blob or "old role" in blob:
            extra_bits.append("prefer recent role/employer evidence")
        if "peerlist" in blob:
            reject_domains.append("peerlist.com")
        # Wrong LinkedIn → reject the slug that was used on that failed draft
        if notes_imply_wrong_linkedin(notes):
            from identity_lock import linkedin_slug

            bad_slug = linkedin_slug(r.get("linkedin_url"))
            if bad_slug and bad_slug not in reject_slugs:
                reject_slugs.append(bad_slug)
            extra_bits.append("LinkedIn URL was wrong — rediscover with name + company/university + LinkedIn keyword")
        if notes and len(notes) > 8:
            # Keep a short freeform exclusion cue for prompts
            clip = notes[:120].replace("\n", " ")
            if clip.lower() not in {p.lower() for p in exclude_phrases}:
                exclude_phrases.append(clip)

    # Dedupe domains
    reject_domains = list(dict.fromkeys(reject_domains))

    query_parts = []
    if prefer_company:
        query_parts.append(prefer_company)
    if prefer_university:
        query_parts.append(prefer_university)
    for slug in reject_slugs[:3]:
        query_parts.append(f'-"{slug}"')
    for phrase in exclude_phrases[:2]:
        # Exa/Google: negative phrasing
        if len(phrase) < 40:
            query_parts.append(f'-"{phrase}"')

    prompt_lines = []
    if reject_slugs or reject_handles or exclude_phrases or prefer_company or prefer_university:
        prompt_lines.append("SEARCH CONSTRAINTS from prior user corrections (apply to every query):")
        if prefer_company:
            prompt_lines.append(f"- Prefer employer/company: {prefer_company}")
        if prefer_university:
            prompt_lines.append(f"- Prefer school/university: {prefer_university}")
        if reject_slugs:
            prompt_lines.append(f"- NEVER use LinkedIn slugs: {', '.join(reject_slugs)}")
        if reject_handles:
            prompt_lines.append(f"- NEVER use social handles: {', '.join('@'+h for h in reject_handles)}")
        if reject_domains:
            prompt_lines.append(f"- Deprioritize/ignore domains: {', '.join(reject_domains)}")
        if exclude_phrases:
            prompt_lines.append(f"- Exclude / do not chase: {'; '.join(exclude_phrases[:5])}")
        for b in extra_bits:
            prompt_lines.append(f"- {b}")

    return {
        "reject_slugs": reject_slugs,
        "reject_handles": reject_handles,
        "reject_domains": reject_domains,
        "exclude_phrases": exclude_phrases,
        "prefer_company": prefer_company,
        "prefer_university": prefer_university,
        "query_suffix": " ".join(query_parts).strip() or None,
        "prompt_block": "\n".join(prompt_lines) if prompt_lines else "",
    }


def merged_search_constraints(
    *,
    name: str,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> dict[str, Any]:
    """Load prior bad feedback and build connector search constraints."""
    rows = prior_bad_corrections(name=name, company=company, linkedin_url=linkedin_url)
    if not rows:
        return {}
    return search_constraints_from_feedback(rows)


def notes_imply_wrong_linkedin(notes: Optional[str]) -> bool:
    blob = (notes or "").lower()
    if not blob:
        return False
    cues = (
        "wrong linkedin",
        "incorrect linkedin",
        "bad linkedin",
        "linkedin is wrong",
        "linkedin wrong",
        "wrong li ",
        "wrong profile url",
        "not their linkedin",
        "different linkedin",
        "linkedin doesn't match",
        "linkedin does not match",
        "mistaken linkedin",
        "false linkedin",
    )
    return any(c in blob for c in cues) or (
        "linkedin" in blob and any(w in blob for w in ("wrong", "incorrect", "not this", "different person"))
    )


def apply_linkedin_correction_from_notes(
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    wrong_notes: Optional[str] = None,
) -> tuple[Optional[str], dict[str, Any]]:
    """If notes say LinkedIn is wrong, reject current slug and rediscover via Gemini.

    Returns (linkedin_url_or_none, meta).
    """
    from identity_lock import linkedin_slug, normalize_linkedin_url

    meta: dict[str, Any] = {"rediscovered": False, "cleared": False}
    notes = (wrong_notes or "").strip()
    current = normalize_linkedin_url(linkedin_url)
    if not notes_imply_wrong_linkedin(notes):
        return current, meta

    reject = []
    if current:
        slug = linkedin_slug(current)
        if slug:
            reject.append(slug)
        meta["cleared"] = True
        meta["rejected_slug"] = slug

    # Also parse any explicit wrong slug from notes
    import re

    for m in re.finditer(r"linkedin\.com/in/([a-z0-9\-_%]{2,80})", notes, re.I):
        reject.append(m.group(1).strip("/").lower())

    print(
        f"  [feedback] wrong LinkedIn flagged — rediscovering for {name!r} "
        f"(reject={reject})",
        flush=True,
    )
    new_url = rediscover_linkedin_via_gemini(
        name=name,
        company=company,
        university=university,
        place=place,
        notes=notes,
        reject_slugs=reject,
    )
    if new_url:
        meta["rediscovered"] = True
        meta["new_linkedin_url"] = new_url
        print(f"  [feedback] rediscovered LinkedIn → {new_url}", flush=True)
        return normalize_linkedin_url(new_url) or new_url, meta

    # Clear bad lock so next research searches fresh with name+extras
    print("  [feedback] LinkedIn rediscovery miss — clearing lock for name search", flush=True)
    return None, meta


def rediscover_linkedin_via_gemini(
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    notes: Optional[str] = None,
    reject_slugs: Optional[List[str]] = None,
) -> Optional[str]:
    """Google Search via Gemini: name + extras + 'linkedin' → exact profile URL."""
    import json
    import os
    import re

    from google import genai
    from google.genai import types

    from gemini_retry import generate_with_retry
    from identity_lock import linkedin_slug, normalize_linkedin_url

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key or not (name or "").strip():
        return None

    extras = []
    if company:
        extras.append(f'company "{company}"')
    if university:
        extras.append(f'university "{university}"')
    if place:
        extras.append(f'location "{place}"')
    if notes:
        extras.append(f"user correction notes: {notes[:240]}")
    reject = [s for s in (reject_slugs or []) if s]
    reject_line = (
        f"Do NOT return these wrong LinkedIn slugs: {', '.join(reject)}."
        if reject
        else ""
    )
    prompt = f"""Use Google Search to find the correct LinkedIn profile for this person.

Name: {name}
Hints: {'; '.join(extras) if extras else '(none)'}
{reject_line}

Search queries like: "{name}" LinkedIn, "{name}" {company or university or ''} site:linkedin.com/in

Return ONLY strict JSON:
{{"linkedin_url": string or null, "confidence": "high"|"medium"|"low", "reason": string}}

Rules:
- linkedin_url must be https://www.linkedin.com/in/... for THIS person
- Prefer profiles whose headline matches the hints
- null if unsure — never invent a slug
"""
    try:
        client = genai.Client(api_key=api_key)
        model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
        resp = generate_with_retry(
            client,
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
        text = (resp.text or "").strip()
        # Extract JSON object
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        data = json.loads(m.group(0))
        url = normalize_linkedin_url(data.get("linkedin_url"))
        if not url:
            return None
        slug = (linkedin_slug(url) or "").lower()
        if slug and slug in {s.lower() for s in reject}:
            return None
        conf = (data.get("confidence") or "").lower()
        if conf == "low":
            return None
        return url
    except Exception as exc:
        print(f"  [feedback] LinkedIn rediscovery error: {exc}", flush=True)
        return None


def _dual_write_supabase(row: dict[str, Any]) -> None:
    try:
        from db import get_supabase

        sb = get_supabase()
        if not sb:
            return
        payload = {
            "id": row["id"],
            "user_id": row.get("user_id"),
            "person_slug": row.get("person_slug"),
            "draft_id": row.get("draft_id"),
            "name": row.get("name"),
            "company": row.get("company"),
            "linkedin_url": row.get("linkedin_url"),
            "rating": row.get("rating"),
            "wrong_notes": row.get("wrong_notes"),
            "wrong_categories": row.get("wrong_categories") or [],
            "briefing_snapshot": row.get("briefing_snapshot"),
            "applied_on_next_research": row.get("applied_on_next_research", False),
            "created_at": row.get("created_at"),
        }
        sb.table("research_feedback").upsert(payload).execute()
    except Exception as e:
        print(f"  [feedback] supabase write skipped: {e}", flush=True)
