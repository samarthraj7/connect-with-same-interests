"""Verify LinkedIn URL matches the claimed name/headline; rediscover on mismatch.

Used after Find Me name-filter (second pass) and when user flags a wrong LinkedIn.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

from identity_lock import linkedin_slug, normalize_linkedin_url
from name_match import is_exact_name_match, name_tokens


def verify_linkedin_matches_identity(
    *,
    name: str,
    linkedin_url: Optional[str],
    company: Optional[str] = None,
    role: Optional[str] = None,
    headline: Optional[str] = None,
    context: Optional[str] = None,
) -> dict[str, Any]:
    """Check whether a LinkedIn URL plausibly belongs to this person.

    Uses Enrich Layer / LinkedIn public OG when available, plus slug vs name tokens.
    Returns status: ok | mismatch | weak | skipped | error
    """
    name = (name or "").strip()
    url = normalize_linkedin_url(linkedin_url)
    if not name:
        return {"status": "skipped", "reason": "no name", "match": False}
    if not url:
        return {"status": "skipped", "reason": "no linkedin_url", "match": False, "needs_rediscover": True}

    slug = (linkedin_slug(url) or "").lower()
    profile_name = None
    profile_headline = None
    source = None

    # 1) Enrich Layer profile (best structured name/headline)
    try:
        from connectors import enrichlayer

        if enrichlayer.configured():
            el = enrichlayer.fetch_profile(url, timeout=12)
            if el.get("status") == "ok":
                profile_name = el.get("full_name") or el.get("name")
                profile_headline = el.get("headline") or el.get("title") or el.get("occupation")
                source = "enrichlayer"
    except Exception as exc:
        print(f"  [li-verify] enrichlayer skip: {exc}", flush=True)

    # 2) Public LinkedIn OG / scrape fallback
    if not profile_name:
        try:
            from connectors import linkedin_public

            pub = linkedin_public.fetch_linkedin_public(url)
            if pub.get("status") == "ok":
                profile_name = pub.get("full_name") or pub.get("name")
                profile_headline = pub.get("headline") or pub.get("title") or pub.get("og_title")
                source = source or "linkedin_public"
        except Exception as exc:
            print(f"  [li-verify] linkedin_public skip: {exc}", flush=True)

    # 3) OpenGraph title parse
    if not profile_name:
        try:
            from connectors.opengraph import fetch_open_graph

            og = fetch_open_graph(url)
            if og.get("status") == "ok":
                title = (og.get("title") or "").strip()
                # "Name - Headline | LinkedIn" or "Name | LinkedIn"
                clean = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title, flags=re.I)
                clean = re.sub(r"\s*-\s*LinkedIn\s*$", "", clean, flags=re.I)
                if " - " in clean:
                    profile_name = clean.split(" - ", 1)[0].strip()
                    profile_headline = profile_headline or clean.split(" - ", 1)[1].strip()
                elif " | " in clean:
                    profile_name = clean.split(" | ", 1)[0].strip()
                else:
                    profile_name = clean or None
                source = source or "opengraph"
        except Exception:
            pass

    name_ok = False
    if profile_name:
        name_ok = is_exact_name_match(name, profile_name) or _soft_name_overlap(name, profile_name)
    else:
        # Fall back to slug containing name tokens (weak)
        name_ok = _slug_matches_name(slug, name)
        if name_ok:
            return {
                "status": "weak",
                "match": True,
                "linkedin_url": url,
                "profile_name": None,
                "profile_headline": profile_headline,
                "reason": "slug loosely matches name; could not load profile title",
                "source": source,
                "needs_rediscover": False,
            }

    headline_ok = _headline_compatible(
        profile_headline or "",
        company=company,
        role=role,
    )

    if name_ok and (headline_ok or not (company or role)):
        return {
            "status": "ok",
            "match": True,
            "linkedin_url": url,
            "profile_name": profile_name,
            "profile_headline": profile_headline,
            "reason": "name and details consistent with LinkedIn profile",
            "source": source,
            "needs_rediscover": False,
        }

    reasons = []
    if not name_ok:
        reasons.append(f"profile name {profile_name!r} ≠ query {name!r}")
    if not headline_ok and (company or role):
        reasons.append(
            f"headline {profile_headline!r} does not reflect "
            f"company/role ({company or '—'} / {role or '—'})"
        )
    return {
        "status": "mismatch",
        "match": False,
        "linkedin_url": url,
        "profile_name": profile_name,
        "profile_headline": profile_headline,
        "reason": "; ".join(reasons) or "identity mismatch",
        "source": source,
        "needs_rediscover": True,
    }


def ensure_candidate_linkedin(candidate: dict, *, query_name: Optional[str] = None) -> dict:
    """Second-pass: verify card LinkedIn vs name/details; Google-rediscover if mismatch.

    Mutates and returns the candidate dict.
    """
    if not isinstance(candidate, dict):
        return candidate
    out = dict(candidate)
    name = (query_name or out.get("name") or "").strip()
    company = (out.get("company") or "").strip() or None
    role = (out.get("role") or "").strip() or None
    context = (out.get("context") or "").strip() or None
    li = (out.get("linkedin_url") or "").strip() or None

    verify = verify_linkedin_matches_identity(
        name=name,
        linkedin_url=li,
        company=company,
        role=role,
        context=context,
    )
    out["linkedin_verify"] = {
        "status": verify.get("status"),
        "match": verify.get("match"),
        "reason": verify.get("reason"),
        "profile_name": verify.get("profile_name"),
        "profile_headline": verify.get("profile_headline"),
    }

    if verify.get("match") and not verify.get("needs_rediscover"):
        if verify.get("profile_headline") and not out.get("role"):
            # Soft-fill role from verified profile headline
            out["role"] = (verify.get("profile_headline") or "")[:120]
        print(
            f"  [li-verify] OK {name!r} → {li} ({verify.get('status')})",
            flush=True,
        )
        return out

    # Mismatch or missing → Google rediscover with name + optional details + LinkedIn keyword
    reject = []
    if li:
        s = linkedin_slug(li)
        if s:
            reject.append(s)

    print(
        f"  [li-verify] MISMATCH/missing for {name!r} — rediscovering "
        f"(reason={verify.get('reason')!r})",
        flush=True,
    )
    from research_feedback import rediscover_linkedin_via_gemini

    # Pack headline chosen on the card into notes so Gemini prefers matching people
    notes_bits = []
    if role:
        notes_bits.append(f"headline/role: {role}")
    if company:
        notes_bits.append(f"company: {company}")
    if context:
        notes_bits.append(f"context: {context[:160]}")
    notes_bits.append("Find the LinkedIn that matches this exact person and headline.")

    new_url = rediscover_linkedin_via_gemini(
        name=name,
        company=company,
        university=None,
        place=None,
        notes=" | ".join(notes_bits),
        reject_slugs=reject,
    )
    if new_url:
        out["linkedin_url"] = new_url
        out["linkedin_locked"] = True
        out["linkedin_verify"]["rediscovered"] = True
        out["linkedin_verify"]["previous_url"] = li
        out["linkedin_verify"]["status"] = "rediscovered"
        out["linkedin_verify"]["match"] = True
        print(f"  [li-verify] rediscovered → {new_url}", flush=True)
        # Re-verify lightly
        again = verify_linkedin_matches_identity(
            name=name, linkedin_url=new_url, company=company, role=role, context=context
        )
        out["linkedin_verify"]["recheck"] = again.get("status")
        if again.get("profile_headline") and not out.get("role"):
            out["role"] = (again.get("profile_headline") or "")[:120]
    else:
        out["linkedin_verify"]["rediscovered"] = False
        if verify.get("needs_rediscover") and verify.get("status") == "mismatch":
            # Drop wrong URL so research doesn't lock the bad identity
            out["linkedin_url_rejected"] = li
            out["linkedin_url"] = None
            print(f"  [li-verify] cleared bad LI for {name!r}", flush=True)

    return out


def verify_candidates_linkedin(candidates: List[dict], *, query_name: str) -> List[dict]:
    """Run ensure_candidate_linkedin on each Find Me card (after name filter)."""
    out = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        try:
            out.append(ensure_candidate_linkedin(c, query_name=query_name))
        except Exception as exc:
            print(f"  [li-verify] candidate error: {exc}", flush=True)
            out.append(dict(c))
    return out


def _soft_name_overlap(query: str, profile: str) -> bool:
    q = name_tokens(query)
    p = name_tokens(profile)
    if len(q) < 2 or len(p) < 1:
        return False
    # First + last of query appear in profile
    return q[0] in p and q[-1] in p


def _slug_matches_name(slug: str, name: str) -> bool:
    if not slug or not name:
        return False
    tokens = name_tokens(name)
    if len(tokens) < 2:
        return tokens[0][:4] in slug if tokens else False
    # Require first token + last token fragment in slug (skips pure random IDs)
    return tokens[0][:3] in slug and tokens[-1][:3] in slug


def _headline_compatible(
    profile_headline: str,
    *,
    company: Optional[str],
    role: Optional[str],
) -> bool:
    """True if we lack company/role hints, or profile headline overlaps them."""
    blob = (profile_headline or "").lower()
    if not company and not role:
        return True
    if not (profile_headline or "").strip():
        # No profile headline loaded — don't fail solely on this
        return True
    hits = 0
    if company:
        co = company.lower().strip()
        # Token overlap for short org names
        if co in blob or any(t in blob for t in re.findall(r"[a-z0-9]{4,}", co)[:3]):
            hits += 1
    if role:
        # Soft: any meaningful role token in headline
        role_toks = [t for t in re.findall(r"[a-z]{4,}", role.lower()) if t not in ("intern", "with", "from")]
        if any(t in blob for t in role_toks[:4]):
            hits += 1
    # Pass if at least one hint matches when we have hints
    need = 1 if (company or role) else 0
    return hits >= need
