"""Hard filter: chosen LinkedIn URL is source of truth for all other scrapes.

Prompt-level identity locks are not enough — same-name Peerlist/GitHub/posts
must be discarded in code before synthesize.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from identity_lock import linkedin_slug, normalize_linkedin_url, same_linkedin


def linkedin_actor_slug(url: Optional[str]) -> Optional[str]:
    """Extract the LinkedIn person slug from /in/, /posts/, or /pulse/ URLs."""
    if not url:
        return None
    u = str(url).strip()
    # Canonical /in/
    s = linkedin_slug(u)
    if s:
        return s
    try:
        path = urlparse(u if "://" in u else "https://" + u).path or ""
    except Exception:
        return None
    parts = [p for p in path.split("/") if p]
    # /posts/{slug}_activity-{id}…  (LinkedIn's standard post URL shape)
    if len(parts) >= 2 and parts[0].lower() == "posts":
        raw = parts[1]
        m = re.search(r"^(.*?)_activity-", raw, flags=re.I)
        if m and m.group(1):
            return m.group(1).lower().rstrip("-_")
        # Rare variants without underscore
        m = re.search(r"^(.*?)-activity-", raw, flags=re.I)
        if m and m.group(1):
            return m.group(1).lower().rstrip("-_")
        m2 = re.match(r"^([A-Za-z0-9][A-Za-z0-9_-]{1,80})", raw)
        if m2:
            return m2.group(1).lower().rstrip("-_")
    return None


def url_conflicts_with_canonical(url: Optional[str], canonical_linkedin: Optional[str]) -> bool:
    """True if URL clearly belongs to a *different* LinkedIn identity."""
    canonical = normalize_linkedin_url(canonical_linkedin)
    if not canonical or not url:
        return False
    want = linkedin_slug(canonical)
    if not want:
        return False
    if "linkedin.com" not in str(url).lower():
        return False
    got = linkedin_actor_slug(url) or linkedin_slug(url)
    if not got:
        return False
    return got != want


def text_declares_other_linkedin(text: Optional[str], canonical_linkedin: Optional[str]) -> bool:
    """True if body text links/mentions a different /in/ slug than canonical."""
    canonical = normalize_linkedin_url(canonical_linkedin)
    want = linkedin_slug(canonical)
    if not want or not text:
        return False
    found = set()
    for m in re.finditer(
        r"linkedin\.com/in/([A-Za-z0-9_-]+)",
        str(text),
        flags=re.I,
    ):
        found.add(m.group(1).lower())
    for m in re.finditer(
        r"linkedin\.com/posts/([A-Za-z0-9_-]+?)(?:_activity|_urn|_|-)",
        str(text),
        flags=re.I,
    ):
        found.add(m.group(1).lower())
    if not found:
        return False
    # Conflict if any declared slug differs and canonical is not among them
    if want in found and len(found) == 1:
        return False
    if want in found:
        # Mixed mentions — still risky; treat as conflict if another slug present
        return any(s != want for s in found)
    return True  # only other slug(s)


def page_corroborates_linkedin(
    *,
    url: Optional[str] = None,
    text: Optional[str] = None,
    canonical_linkedin: Optional[str],
    company: Optional[str] = None,
    university: Optional[str] = None,
) -> bool:
    """Keep a non-LinkedIn page only if it ties to the chosen LinkedIn identity.

    Corroboration = mentions canonical slug OR (company/university token AND no other LI slug).
    """
    canonical = normalize_linkedin_url(canonical_linkedin)
    want = linkedin_slug(canonical)
    if not want:
        return True  # no lock — cannot filter
    blob = f"{url or ''}\n{text or ''}".lower()
    if want in blob or (canonical and canonical.lower() in blob):
        return not text_declares_other_linkedin(blob, canonical)
    if text_declares_other_linkedin(blob, canonical):
        return False
    if url_conflicts_with_canonical(url, canonical):
        return False
    # Soft: employer/school token present and no conflicting LI
    org_hit = False
    for org in (company, university):
        if not org:
            continue
        tokens = [t for t in re.findall(r"[a-z]{4,}", org.lower()) if t not in ("university", "college", "institute")]
        if tokens and all(t in blob for t in tokens[:2]):
            org_hit = True
            break
        # single distinctive token
        for t in tokens:
            if len(t) >= 5 and t in blob:
                org_hit = True
                break
    return org_hit


def filter_sources_against_linkedin(
    *,
    linkedin_url: Optional[str],
    sources: dict[str, Any],
    company: Optional[str] = None,
    university: Optional[str] = None,
    name: Optional[str] = None,
) -> dict[str, Any]:
    """Return a deep-copied sources dict with conflicting same-name noise removed."""
    canonical = normalize_linkedin_url(linkedin_url)
    if not canonical:
        return sources

    out = copy.deepcopy(sources or {})
    dropped: list[str] = []

    # ── GitHub: require confirmed identity_match OR self-declared same LinkedIn
    gh = out.get("github")
    if isinstance(gh, dict) and gh.get("status") in ("ok", "ambiguous", "partial"):
        im = gh.get("identity_match") or {}
        tier = (im.get("tier") or "").lower()
        declared = []
        for sa in gh.get("social_accounts") or []:
            if isinstance(sa, dict) and (
                "linkedin" in (sa.get("provider") or "").lower()
                or "linkedin.com" in (sa.get("url") or "").lower()
            ):
                declared.append(sa.get("url"))
        blog = (gh.get("profile") or {}).get("blog") or gh.get("blog")
        if blog:
            declared.append(blog)
        conflict = any(
            text_declares_other_linkedin(str(d), canonical)
            or (normalize_linkedin_url(d) and not same_linkedin(d, canonical))
            for d in declared
            if d and "linkedin" in str(d).lower()
        )
        if tier == "confirmed" and not conflict:
            pass
        elif conflict or tier in ("no_match", "possible", "") or not im:
            dropped.append(
                f"github:{ (gh.get('profile') or {}).get('login') or gh.get('username') } "
                f"tier={tier or 'none'} conflict={conflict}"
            )
            out["github"] = {
                "status": "rejected_identity",
                "reason": "GitHub profile does not corroborate chosen LinkedIn",
                "identity_match": im or None,
                "rejected_login": (gh.get("profile") or {}).get("login") or gh.get("username"),
            }

    # ── Gemini search: drop posts/sources that cite other LinkedIn / peerlist of other person
    gem = out.get("gemini_search")
    if isinstance(gem, dict) and gem.get("status") == "ok":
        gem = _filter_gemini_blob(gem, canonical, company=company, university=university)
        out["gemini_search"] = gem
        if gem.get("_identity_dropped"):
            dropped.append(f"gemini_search:{gem['_identity_dropped']}")

    # ── personal_info evidence
    pi = out.get("personal_info")
    if isinstance(pi, dict):
        pi2, n = _filter_personal_info(pi, canonical, company=company, university=university)
        out["personal_info"] = pi2
        if n:
            dropped.append(f"personal_info:{n}")

    # ── public_web hits
    pw = out.get("public_web") or out.get("public_presence")
    key = "public_web" if "public_web" in out else ("public_presence" if "public_presence" in out else None)
    if key and isinstance(pw, dict):
        pw2, n = _filter_public_web(pw, canonical, company=company, university=university)
        out[key] = pw2
        if n:
            dropped.append(f"{key}:{n}")

    # ── deep_agent evidence
    da = out.get("deep_agent")
    if isinstance(da, dict):
        da2, n = _filter_deep_agent(da, canonical, company=company, university=university)
        out["deep_agent"] = da2
        if n:
            dropped.append(f"deep_agent:{n}")

    # ── exa mentions with other LI
    exa = out.get("exa_search")
    if isinstance(exa, dict):
        exa2, n = _filter_exa(exa, canonical)
        out["exa_search"] = exa2
        if n:
            dropped.append(f"exa_search:{n}")

    # ── socials: demote if match is face-only weak / wrong person notes
    for sk in ("instagram_public", "twitter_public", "facebook_public"):
        block = out.get(sk)
        if not isinstance(block, dict):
            continue
        if block.get("status") == "ok":
            conf = (block.get("match_confidence") or "").lower()
            face = block.get("face_match") or {}
            accepted = face.get("accepted")
            # Require strong corroboration when LinkedIn locked
            if conf not in ("high", "medium") and not accepted:
                dropped.append(f"{sk}:weak_match")
                out[sk] = {
                    **block,
                    "status": "ambiguous",
                    "reason": "LinkedIn lock set — social match not corroborated",
                }
            elif accepted and conf == "low":
                # Face alone is not enough with LI lock
                dropped.append(f"{sk}:face_without_text")
                out[sk] = {
                    **block,
                    "status": "ambiguous",
                    "reason": "Face score alone cannot attach social when LinkedIn is locked",
                    "match_confidence": "low",
                }

    # ── Nimble page extracts: drop pages that cite another LinkedIn identity
    np = out.get("nimble_pages")
    if isinstance(np, dict) and isinstance(np.get("pages"), list):
        kept = []
        n_drop = 0
        for p in np["pages"]:
            if not isinstance(p, dict):
                continue
            blob = f"{p.get('url') or ''}\n{p.get('final_url') or ''}\n{p.get('markdown') or p.get('text') or ''}"
            if text_declares_other_linkedin(blob, canonical) or url_conflicts_with_canonical(
                p.get("url") or p.get("final_url"), canonical
            ):
                n_drop += 1
                continue
            kept.append(p)
        if n_drop:
            dropped.append(f"nimble_pages:{n_drop}")
            np = {**np, "pages": kept}
            if not kept and np.get("status") == "ok":
                np["status"] = "filtered"
                np["reason"] = "all Nimble pages conflicted with canonical LinkedIn"
            out["nimble_pages"] = np

    # ── Licensed enrichment: drop if LinkedIn URL conflicts with canonical
    for ek in ("apollo", "aleads", "enrichlayer"):
        block = out.get(ek)
        if not isinstance(block, dict):
            continue
        other = block.get("linkedin_url") or (block.get("profile") or {}).get("linkedin_url")
        if other and not same_linkedin(other, canonical):
            dropped.append(f"{ek}:linkedin_mismatch")
            out[ek] = {
                "status": "rejected_identity",
                "reason": "enrichment LinkedIn does not match chosen identity",
            }

    if dropped:
        print(
            f"  [identity_filter] canonical={canonical} dropped={dropped}",
            flush=True,
        )
        out["_identity_filter"] = {
            "canonical_linkedin": canonical,
            "dropped": dropped,
        }
    return out


def _filter_gemini_blob(gem: dict, canonical: str, *, company, university) -> dict:
    dropped = 0
    links = gem.get("social_profile_links") or {}
    other = links.get("linkedin") if isinstance(links, dict) else None
    if other and not same_linkedin(other, canonical):
        return {
            "status": "rejected_identity",
            "reason": f"Gemini angle LinkedIn {other} ≠ canonical",
            "_identity_dropped": 1,
        }

    # Filter public posts by source_url conflict
    posts = []
    for p in gem.get("public_posts_or_writing") or []:
        if not isinstance(p, dict):
            continue
        src = p.get("source_url") or p.get("source") or ""
        snippet = p.get("snippet") or p.get("topic") or ""
        if url_conflicts_with_canonical(str(src), canonical) or text_declares_other_linkedin(
            f"{src}\n{snippet}", canonical
        ):
            dropped += 1
            continue
        if str(src) and not page_corroborates_linkedin(
            url=str(src), text=str(snippet), canonical_linkedin=canonical, company=company, university=university
        ):
            # Keep career/education from gemini; drop uncorroborated posts only
            if "peerlist" in str(src).lower() or "peerlist" in str(snippet).lower():
                dropped += 1
                continue
        posts.append(p)
    gem = dict(gem)
    gem["public_posts_or_writing"] = posts

    # Filter verified_pages / sources lists
    pages = []
    for pg in gem.get("verified_pages") or []:
        if not isinstance(pg, dict):
            continue
        u = pg.get("url") or ""
        blob = f"{u} {pg.get('title') or ''} {pg.get('description') or ''}"
        if url_conflicts_with_canonical(u, canonical) or text_declares_other_linkedin(blob, canonical):
            dropped += 1
            continue
        if "peerlist.io" in u.lower() and not page_corroborates_linkedin(
            url=u, text=blob, canonical_linkedin=canonical, company=company, university=university
        ):
            dropped += 1
            continue
        pages.append(pg)
    gem["verified_pages"] = pages

    srcs = []
    for s in gem.get("sources") or []:
        if isinstance(s, dict):
            u = s.get("url") or ""
            title = s.get("title") or ""
        else:
            u, title = str(s), ""
        blob = f"{u} {title}"
        if url_conflicts_with_canonical(u, canonical) or text_declares_other_linkedin(blob, canonical):
            dropped += 1
            continue
        if "peerlist.io" in u.lower() and linkedin_slug(canonical) not in blob.lower():
            dropped += 1
            continue
        srcs.append(s)
    gem["sources"] = srcs

    # Bio / career pollution heuristic: if bio_summary mentions peerlist handle of other person
    bio = gem.get("bio_summary") or ""
    if text_declares_other_linkedin(bio, canonical):
        gem["bio_summary"] = ""
        dropped += 1
        gem["bio_summary_note"] = "cleared — referenced another LinkedIn identity"

    # Scrub career / education / colleagues that cite another LinkedIn or fail org corroboration
    for field in ("career_history", "education", "awards_and_recognitions"):
        items = gem.get(field) or []
        if not isinstance(items, list):
            continue
        kept_f = []
        for item in items:
            text = item if isinstance(item, str) else json.dumps(item, default=str)
            if text_declares_other_linkedin(text, canonical):
                dropped += 1
                continue
            kept_f.append(item)
        gem[field] = kept_f

    for field in ("notable_colleagues", "senior_colleagues"):
        items = gem.get(field) or []
        if not isinstance(items, list):
            continue
        kept_f = []
        for item in items:
            if not isinstance(item, dict):
                continue
            blob = f"{item.get('name') or ''} {item.get('context') or ''} {item.get('title') or ''}"
            if text_declares_other_linkedin(blob, canonical):
                dropped += 1
                continue
            kept_f.append(item)
        gem[field] = kept_f

    # Force canonical on links
    links = dict(gem.get("social_profile_links") or {})
    links["linkedin"] = canonical
    # Drop github link unless it will be validated separately — leave for github filter
    gem["social_profile_links"] = links
    if dropped:
        gem["_identity_dropped"] = dropped
    return gem


def _filter_personal_info(pi: dict, canonical: str, *, company, university) -> tuple[dict, int]:
    dropped = 0
    evidence = []
    for e in pi.get("evidence") or []:
        if isinstance(e, dict):
            hint = f"{e.get('fact') or ''} {e.get('source_hint') or ''}"
        else:
            hint = str(e)
        if text_declares_other_linkedin(hint, canonical):
            dropped += 1
            continue
        # Peerlist / wrong github name without org corroboration
        low = hint.lower()
        if "peerlist" in low and linkedin_slug(canonical) not in low:
            if not page_corroborates_linkedin(
                text=hint, canonical_linkedin=canonical, company=company, university=university
            ):
                dropped += 1
                continue
        evidence.append(e)
    pi = dict(pi)
    pi["evidence"] = evidence
    # Clear location if evidence overwhelmingly from rejected set and conflicts with company geo — soft
    return pi, dropped


def _filter_public_web(pw: dict, canonical: str, *, company, university) -> tuple[dict, int]:
    dropped = 0

    def keep_hit(h: dict) -> bool:
        nonlocal dropped
        u = h.get("url") or ""
        text = f"{h.get('title') or ''} {h.get('text') or h.get('snippet') or ''}"
        if url_conflicts_with_canonical(u, canonical) or text_declares_other_linkedin(f"{u}\n{text}", canonical):
            dropped += 1
            return False
        if not page_corroborates_linkedin(
            url=u, text=text, canonical_linkedin=canonical, company=company, university=university
        ):
            # Require corroboration when LI lock exists
            dropped += 1
            return False
        return True

    pw = dict(pw)
    for key in ("portfolios", "blogs", "other", "results", "hits"):
        if isinstance(pw.get(key), list):
            before = pw[key]
            pw[key] = [h for h in before if isinstance(h, dict) and keep_hit(h)]
    return pw, dropped


def _filter_deep_agent(da: dict, canonical: str, *, company, university) -> tuple[dict, int]:
    dropped = 0
    evidence = []
    for fact in da.get("evidence") or da.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        src = fact.get("source_url") or fact.get("url") or ""
        text = fact.get("fact") or fact.get("text") or ""
        if url_conflicts_with_canonical(src, canonical) or text_declares_other_linkedin(
            f"{src}\n{text}", canonical
        ):
            dropped += 1
            continue
        if not page_corroborates_linkedin(
            url=src, text=text, canonical_linkedin=canonical, company=company, university=university
        ):
            # deep_agent facts should cite pages tied to identity; drop weak ones when lock set
            if "peerlist" in f"{src} {text}".lower() or "github.com/" in src.lower():
                dropped += 1
                continue
        evidence.append(fact)
    da = dict(da)
    if "evidence" in da:
        da["evidence"] = evidence
    if "facts" in da:
        da["facts"] = evidence
    # Filter retrieved urls list if present
    if isinstance(da.get("urls"), list):
        kept = []
        for u in da["urls"]:
            if url_conflicts_with_canonical(u, canonical):
                dropped += 1
                continue
            kept.append(u)
        da["urls"] = kept
    return da, dropped


def _filter_exa(exa: dict, canonical: str) -> tuple[dict, int]:
    dropped = 0
    exa = dict(exa)
    if exa.get("linkedin_url") and not same_linkedin(exa.get("linkedin_url"), canonical):
        exa["linkedin_url"] = canonical
        exa["identity_note"] = "forced to canonical LinkedIn"
    for key in ("mentions", "general_results", "results"):
        rows = exa.get(key)
        if not isinstance(rows, list):
            continue
        kept = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            u = r.get("url") or r.get("linkedin_url") or ""
            text = f"{r.get('title') or ''} {r.get('text') or r.get('snippet') or ''}"
            if url_conflicts_with_canonical(u, canonical) or text_declares_other_linkedin(
                f"{u}\n{text}", canonical
            ):
                dropped += 1
                continue
            kept.append(r)
        exa[key] = kept
    return exa, dropped
