"""LinkedIn ↔ GitHub (and similar) identity disambiguation.

Never auto-merge on name alone. Always return score + evidence + tier.
Confirmed matches require score >= 0.5 AND at least one Direct signal.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional
from urllib.parse import urlparse

import requests

from identity_lock import normalize_linkedin_url, same_linkedin, linkedin_slug

# ── weights (must match product spec) ───────────────────────────────────────

WEIGHTS = {
    "self_declared_link": 0.5,  # Direct
    "personal_website_both": 0.3,  # Direct
    "avatar_face_match": 0.55,  # Direct — LinkedIn/research photo vs GitHub avatar
    "employer_match": 0.15,  # Inferred
    "location_match": 0.05,  # Inferred
    "username_email_overlap": 0.15,  # Inferred
    "bio_niche_overlap": 0.05,  # Weak inferred
}

DIRECT_SIGNALS = frozenset({"self_declared_link", "personal_website_both", "avatar_face_match"})

TIER_CONFIRMED = "confirmed"
TIER_POSSIBLE = "possible"
TIER_NO_MATCH = "no_match"

_QUEUE_DIR = Path(__file__).resolve().parent / "profiles" / "_identity_queue"
_LOG_DIR = Path(__file__).resolve().parent / "profiles" / "_identity_resolve_logs"


# ── 1) Fetch layer ──────────────────────────────────────────────────────────


def fetch_linkedin_identity(
    *,
    linkedin_url: Optional[str] = None,
    name: Optional[str] = None,
    company: Optional[str] = None,
    location: Optional[str] = None,
    headline: Optional[str] = None,
    about: Optional[str] = None,
    website: Optional[str] = None,
    email: Optional[str] = None,
    sources: Optional[dict] = None,
) -> dict[str, Any]:
    """Normalize LinkedIn-side fields from URL scrape and/or research sources."""
    url = normalize_linkedin_url(linkedin_url) or (linkedin_url or "").strip() or None
    out: dict[str, Any] = {
        "platform": "linkedin",
        "url": url,
        "name": (name or "").strip() or None,
        "company": (company or "").strip() or None,
        "location": (location or "").strip() or None,
        "headline": (headline or "").strip() or None,
        "about": (about or "").strip() or None,
        "website": (website or "").strip() or None,
        "email": (email or "").strip() or None,
        "github_urls": [],
        "raw_links": [],
    }

    sources = sources or {}
    # Prefer richer public scrape when enabled
    if url:
        try:
            from connectors import linkedin_public

            pub = linkedin_public.fetch_linkedin_public(url)
            if pub.get("status") == "ok":
                out["headline"] = out["headline"] or pub.get("headline")
                out["about"] = out["about"] or pub.get("about")
                out["featured_titles"] = pub.get("featured_titles") or []
                out["photo_url"] = pub.get("photo_url") or pub.get("profile_pic_url")
        except Exception as exc:
            out["linkedin_public_error"] = str(exc)[:200]

    # Fold research / enrich sources
    gemini = sources.get("gemini_search") or {}
    if isinstance(gemini, dict):
        out["name"] = out["name"] or gemini.get("name")
        out["company"] = out["company"] or gemini.get("current_company")
        out["headline"] = out["headline"] or gemini.get("current_role")
        out["photo_url"] = out.get("photo_url") or gemini.get("photo_url")
        links = gemini.get("social_profile_links") or {}
        if isinstance(links, dict):
            if links.get("github"):
                out["github_urls"].append(links["github"])
            if links.get("website") and not out["website"]:
                out["website"] = links["website"]
    apollo = sources.get("apollo") or {}
    if isinstance(apollo, dict) and apollo.get("status") == "ok":
        out["company"] = out["company"] or (apollo.get("organization") or {}).get("name") or apollo.get(
            "organization_name"
        )
        out["email"] = out["email"] or apollo.get("email")
        if apollo.get("github_url"):
            out["github_urls"].append(apollo["github_url"])
        loc_bits = [apollo.get("city"), apollo.get("state")]
        loc = ", ".join(x for x in loc_bits if x)
        if loc and not out["location"]:
            out["location"] = loc
    exa = sources.get("exa_search") or {}
    if isinstance(exa, dict):
        out["company"] = out["company"] or exa.get("company")
        out["headline"] = out["headline"] or exa.get("role") or exa.get("title")

    # Pull URLs mentioned in about/headline
    blob = " ".join(x for x in [out.get("about"), out.get("headline"), out.get("website")] if x)
    out["github_urls"].extend(_extract_github_urls(blob))
    out["github_urls"] = _uniq(out["github_urls"])
    out["raw_links"] = list(out["github_urls"])
    if out.get("website"):
        out["raw_links"].append(out["website"])
    return out


def fetch_github_identity(
    *,
    username: Optional[str] = None,
    profile: Optional[dict] = None,
    social_accounts: Optional[list] = None,
    organizations: Optional[list] = None,
    repos: Optional[list] = None,
    html_url: Optional[str] = None,
) -> dict[str, Any]:
    """Normalize GitHub-side fields (fetch if only username given)."""
    from connectors import github as gh

    login = (username or "").strip().lstrip("@") or None
    prof = dict(profile or {})
    if login and not prof:
        prof = gh._fetch_user(login) or {}
    if not login:
        login = (prof.get("login") or "").strip() or None
    if not login:
        return {"platform": "github", "status": "error", "error": "username required"}

    graph_socials = list(social_accounts or [])
    orgs = list(organizations or [])
    repo_list = list(repos or [])
    if not graph_socials or not orgs:
        graph = gh._fetch_graphql(login)
        if graph:
            if not graph_socials:
                graph_socials = graph.get("social_accounts") or []
            if not orgs:
                orgs = graph.get("organizations") or []
    if not repo_list:
        repo_list = gh._fetch_top_repos(login)

    url = html_url or prof.get("html_url") or f"https://github.com/{login}"
    blog = (prof.get("blog") or "").strip() or None
    if blog and not blog.startswith("http"):
        blog = "https://" + blog

    linkedin_urls = []
    for sa in graph_socials:
        if not isinstance(sa, dict):
            continue
        provider = (sa.get("provider") or "").upper()
        sa_url = sa.get("url") or ""
        if "LINKEDIN" in provider or "linkedin.com" in sa_url.lower():
            norm = normalize_linkedin_url(sa_url) or sa_url
            if norm:
                linkedin_urls.append(norm)
    if blog:
        linkedin_urls.extend(_extract_linkedin_urls(blog))
    bio = prof.get("bio") or ""
    linkedin_urls.extend(_extract_linkedin_urls(bio))
    linkedin_urls = _uniq(linkedin_urls)

    return {
        "platform": "github",
        "status": "ok",
        "url": url,
        "login": login,
        "name": prof.get("name"),
        "company": _clean_gh_company(prof.get("company")),
        "location": prof.get("location"),
        "bio": bio or None,
        "blog": blog,
        "email": (prof.get("email") or "").strip() or None,
        "twitter_username": prof.get("twitter_username"),
        "social_accounts": graph_socials,
        "organizations": orgs,
        "repos": repo_list,
        "linkedin_urls": linkedin_urls,
        "avatar_url": prof.get("avatar_url"),
        "raw_links": _uniq([blog] + linkedin_urls + [url] if blog else linkedin_urls + [url]),
    }


# ── 2) Signal extractor ─────────────────────────────────────────────────────


def _avatar_face_signal(
    linkedin: dict[str, Any],
    github: dict[str, Any],
    *,
    sources: Optional[dict] = None,
) -> Optional[dict[str, Any]]:
    """Direct signal when LinkedIn/research photo and GitHub avatar are the same face."""
    ref = (linkedin.get("photo_url") or "").strip()
    sources = sources or {}
    if not ref:
        for key in ("apollo", "enrichlayer", "linkedin_public", "gemini_search"):
            block = sources.get(key)
            if isinstance(block, dict):
                ref = (
                    block.get("photo_url")
                    or block.get("profile_pic_url")
                    or (block.get("profile") or {}).get("profile_pic_url")
                    or ""
                ).strip()
                if ref:
                    break
    avatar = (github.get("avatar_url") or "").strip()
    if not ref or not avatar or "ui-avatars.com" in ref:
        return None
    try:
        from face_match import compare_faces

        result = compare_faces(
            ref,
            [
                {
                    "handle": github.get("login") or "github",
                    "full_name": github.get("name"),
                    "photo_url": avatar,
                    "profile_url": github.get("url"),
                }
            ],
            person_name=linkedin.get("name"),
        )
        accepted = (result or {}).get("accepted")
        if accepted and int(accepted.get("score") or 0) >= 85:
            return {
                "signal": "avatar_face_match",
                "kind": "direct",
                "weight": WEIGHTS["avatar_face_match"],
                "evidence": [
                    f"Face match score {accepted.get('score')} between LinkedIn/research photo and GitHub avatar"
                ],
            }
    except Exception as exc:
        print(f"  [identity_resolve] avatar face match skipped: {exc}", flush=True)
    return None


def extract_signals(
    linkedin: dict[str, Any],
    github: dict[str, Any],
    *,
    known_email: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Compute each weighted signal independently with evidence strings."""
    signals: List[dict[str, Any]] = []
    li_url = linkedin.get("url")
    gh_url = github.get("url") or (f"https://github.com/{github.get('login')}" if github.get("login") else None)
    known_email = (known_email or linkedin.get("email") or "").strip().lower() or None

    # Direct: self-declared cross-link
    self_ev = _self_declared_evidence(linkedin, github, li_url, gh_url)
    if self_ev:
        signals.append(
            {
                "signal": "self_declared_link",
                "kind": "direct",
                "weight": WEIGHTS["self_declared_link"],
                "evidence": self_ev,
            }
        )

    # Direct: personal website lists both
    site_ev = _personal_website_both_evidence(linkedin, github, li_url, gh_url)
    if site_ev:
        signals.append(
            {
                "signal": "personal_website_both",
                "kind": "direct",
                "weight": WEIGHTS["personal_website_both"],
                "evidence": site_ev,
            }
        )

    # Inferred: employer
    emp_ev = _employer_match_evidence(linkedin, github)
    if emp_ev:
        signals.append(
            {
                "signal": "employer_match",
                "kind": "inferred",
                "weight": WEIGHTS["employer_match"],
                "evidence": emp_ev,
            }
        )

    # Inferred: location
    loc_ev = _location_match_evidence(linkedin, github)
    if loc_ev:
        signals.append(
            {
                "signal": "location_match",
                "kind": "inferred",
                "weight": WEIGHTS["location_match"],
                "evidence": loc_ev,
            }
        )

    # Inferred: username / email
    ue_ev = _username_email_evidence(linkedin, github, known_email=known_email)
    if ue_ev:
        signals.append(
            {
                "signal": "username_email_overlap",
                "kind": "inferred",
                "weight": WEIGHTS["username_email_overlap"],
                "evidence": ue_ev,
            }
        )

    # Weak: bio / niche
    bio_ev = _bio_niche_evidence(linkedin, github)
    if bio_ev:
        signals.append(
            {
                "signal": "bio_niche_overlap",
                "kind": "weak_inferred",
                "weight": WEIGHTS["bio_niche_overlap"],
                "evidence": bio_ev,
            }
        )

    return signals


# ── 3) Scorer ───────────────────────────────────────────────────────────────


def score_match(signals: List[dict[str, Any]]) -> dict[str, Any]:
    """Sum weights, assign tier. Confirmed requires Direct signal."""
    score = round(sum(float(s.get("weight") or 0) for s in signals), 4)
    # Cap at 1.0 for readability (weights can theoretically exceed)
    score = min(score, 1.0)
    has_direct = any(s.get("signal") in DIRECT_SIGNALS for s in signals)
    evidence = []
    for s in signals:
        ev = s.get("evidence")
        if isinstance(ev, list):
            evidence.extend(str(x) for x in ev)
        elif ev:
            evidence.append(str(ev))

    if score >= 0.5 and has_direct:
        tier = TIER_CONFIRMED
    elif score >= 0.2:
        tier = TIER_POSSIBLE
    else:
        tier = TIER_NO_MATCH

    # Hard constraint: without Direct, never confirmed (even if weights somehow >= 0.5)
    if tier == TIER_CONFIRMED and not has_direct:
        tier = TIER_POSSIBLE

    return {
        "score": score,
        "tier": tier,
        "has_direct": has_direct,
        "evidence": evidence,
        "signals": signals,
    }


# ── 4) Output layer ─────────────────────────────────────────────────────────


def resolve_linkedin_github(
    *,
    linkedin_url: Optional[str] = None,
    github_url: Optional[str] = None,
    github_username: Optional[str] = None,
    name: Optional[str] = None,
    company: Optional[str] = None,
    location: Optional[str] = None,
    known_email: Optional[str] = None,
    linkedin_identity: Optional[dict] = None,
    github_identity: Optional[dict] = None,
    sources: Optional[dict] = None,
    enqueue_possible: bool = True,
) -> dict[str, Any]:
    """Score one LinkedIn ↔ GitHub candidate pair. Structured JSON only."""
    li = linkedin_identity or fetch_linkedin_identity(
        linkedin_url=linkedin_url,
        name=name,
        company=company,
        location=location,
        email=known_email,
        sources=sources,
    )
    gh_login = github_username or _github_login_from_url(github_url)
    gh = github_identity or fetch_github_identity(username=gh_login)
    if gh.get("status") == "error":
        out = {
            "linkedin_url": li.get("url"),
            "candidate_url": github_url or (f"https://github.com/{gh_login}" if gh_login else None),
            "score": 0.0,
            "tier": TIER_NO_MATCH,
            "evidence": [f"GitHub fetch failed: {gh.get('error')}"],
        }
        log_decision(out, reason="github_fetch_failed")
        return out

    signals = extract_signals(li, gh, known_email=known_email or li.get("email"))
    # Face match LinkedIn/research photo vs GitHub avatar — Direct signal when strong
    face_sig = _avatar_face_signal(li, gh, sources=sources)
    if face_sig:
        signals.append(face_sig)
    scored = score_match(signals)

    # Name-only path: if we somehow only had name similarity with no signals, stay no_match.
    # If the pair was discovered via name search and has zero Direct, never upgrade to confirmed
    # (already enforced). Cap: name presence alone must not invent weight — we add none.

    out = {
        "linkedin_url": li.get("url"),
        "candidate_url": gh.get("url"),
        "score": scored["score"],
        "tier": scored["tier"],
        "evidence": scored["evidence"],
    }
    # Extra structured fields for audit (not free-text identity claims)
    out["_meta"] = {
        "has_direct": scored["has_direct"],
        "signals": [
            {"signal": s["signal"], "kind": s["kind"], "weight": s["weight"]} for s in scored["signals"]
        ],
        "github_login": gh.get("login"),
        "name_only": _is_name_only_pair(li, gh, scored["signals"]),
    }
    if out["_meta"]["name_only"] and out["tier"] == TIER_CONFIRMED:
        out["tier"] = TIER_POSSIBLE
        out["evidence"] = list(out["evidence"]) + [
            "Downgraded: name-only pairs cannot be confirmed"
        ]

    log_decision(out, reason="resolve")
    if enqueue_possible and out["tier"] == TIER_POSSIBLE:
        queue_possible(out, linkedin=li, github=gh)
    return {k: v for k, v in out.items() if not k.startswith("_") or k == "_meta"}


def resolve_github_candidates(
    *,
    linkedin_url: Optional[str],
    candidates: List[dict[str, Any]],
    name: Optional[str] = None,
    company: Optional[str] = None,
    location: Optional[str] = None,
    known_email: Optional[str] = None,
    sources: Optional[dict] = None,
) -> List[dict[str, Any]]:
    """Score many GitHub candidate profiles against one LinkedIn identity."""
    li = fetch_linkedin_identity(
        linkedin_url=linkedin_url,
        name=name,
        company=company,
        location=location,
        email=known_email,
        sources=sources,
    )
    results = []
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        login = cand.get("login") or cand.get("username")
        gh = fetch_github_identity(
            username=login,
            profile=cand.get("profile") or cand,
            social_accounts=cand.get("social_accounts"),
            organizations=cand.get("organizations"),
            repos=cand.get("repos"),
            html_url=cand.get("html_url") or cand.get("url"),
        )
        resolution = resolve_linkedin_github(
            linkedin_identity=li,
            github_identity=gh,
            known_email=known_email,
            enqueue_possible=True,
        )
        results.append(resolution)
        # Always log non-merges too (resolve already logs; reinforce for clarity)
        if resolution.get("tier") != TIER_CONFIRMED:
            log_decision(resolution, reason="non_merge_candidate")
    results.sort(key=lambda r: -float(r.get("score") or 0))
    return results


def public_resolution(row: dict[str, Any]) -> dict[str, Any]:
    """Strip internal _meta for API responses while keeping required fields."""
    return {
        "linkedin_url": row.get("linkedin_url"),
        "candidate_url": row.get("candidate_url"),
        "score": row.get("score"),
        "tier": row.get("tier"),
        "evidence": list(row.get("evidence") or []),
    }


# ── 5) HITL queue + audit log ───────────────────────────────────────────────


def queue_possible(
    resolution: dict[str, Any],
    *,
    linkedin: Optional[dict] = None,
    github: Optional[dict] = None,
) -> str:
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    qid = uuid.uuid4().hex
    rec = {
        "id": qid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_review",
        "resolution": public_resolution(resolution),
        "linkedin_snapshot": {
            "url": (linkedin or {}).get("url"),
            "name": (linkedin or {}).get("name"),
            "company": (linkedin or {}).get("company"),
        },
        "github_snapshot": {
            "url": (github or {}).get("url"),
            "login": (github or {}).get("login"),
            "name": (github or {}).get("name"),
            "company": (github or {}).get("company"),
        },
    }
    (_QUEUE_DIR / f"{qid}.json").write_text(json.dumps(rec, indent=2))
    print(
        f"  [identity_resolve] QUEUED possible {qid} "
        f"li={rec['resolution'].get('linkedin_url')!r} "
        f"gh={rec['resolution'].get('candidate_url')!r} "
        f"score={rec['resolution'].get('score')}",
        flush=True,
    )
    return qid


def list_possible_queue(*, limit: int = 50) -> List[dict[str, Any]]:
    if not _QUEUE_DIR.exists():
        return []
    rows = []
    for path in sorted(_QUEUE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rows.append(json.loads(path.read_text()))
        except Exception:
            continue
        if len(rows) >= limit:
            break
    return rows


def resolve_queue_item(qid: str, decision: str) -> Optional[dict[str, Any]]:
    """Human decision: confirm | reject."""
    path = _QUEUE_DIR / f"{(qid or '').strip()}.json"
    if not path.exists():
        return None
    rec = json.loads(path.read_text())
    rec["status"] = "confirmed" if decision == "confirm" else "rejected"
    rec["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(rec, indent=2))
    log_decision(
        rec.get("resolution") or {},
        reason=f"hitl_{rec['status']}",
    )
    return rec


def log_decision(resolution: dict[str, Any], *, reason: str = "") -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = _LOG_DIR / f"{day}.jsonl"
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "linkedin_url": resolution.get("linkedin_url"),
        "candidate_url": resolution.get("candidate_url"),
        "score": resolution.get("score"),
        "tier": resolution.get("tier"),
        "evidence": resolution.get("evidence") or [],
        "merged": resolution.get("tier") == TIER_CONFIRMED,
    }
    with path.open("a") as f:
        f.write(json.dumps(line) + "\n")
    print(
        f"  [identity_resolve] {line['tier']} score={line['score']} "
        f"merged={line['merged']} reason={reason} "
        f"li={line['linkedin_url']!r} gh={line['candidate_url']!r} "
        f"evidence_n={len(line['evidence'])}",
        flush=True,
    )


# ── signal helpers ──────────────────────────────────────────────────────────


def _self_declared_evidence(
    linkedin: dict, github: dict, li_url: Optional[str], gh_url: Optional[str]
) -> List[str]:
    ev = []
    # GitHub → LinkedIn
    for u in github.get("linkedin_urls") or []:
        if li_url and same_linkedin(u, li_url):
            ev.append(f"GitHub social/blog links to same LinkedIn ({u})")
        elif li_url and linkedin_slug(u) and linkedin_slug(u) == linkedin_slug(li_url):
            ev.append(f"GitHub links to LinkedIn slug match ({u})")
    # LinkedIn → GitHub
    gh_login = (github.get("login") or "").lower()
    for u in linkedin.get("github_urls") or []:
        login = _github_login_from_url(u)
        if login and gh_login and login.lower() == gh_login:
            ev.append(f"LinkedIn lists GitHub @{login}")
        elif gh_url and gh_url.rstrip("/").lower() in (u or "").rstrip("/").lower():
            ev.append(f"LinkedIn lists GitHub URL {u}")
    return ev


def _personal_website_both_evidence(
    linkedin: dict, github: dict, li_url: Optional[str], gh_url: Optional[str]
) -> List[str]:
    sites = []
    for s in (linkedin.get("website"), github.get("blog")):
        if s and _looks_like_personal_site(s):
            sites.append(s)
    sites = _uniq(sites)
    ev = []
    for site in sites:
        html = _fetch_text(site)
        if not html:
            continue
        has_li = bool(li_url and (linkedin_slug(li_url) or "") and (linkedin_slug(li_url) or "") in html.lower())
        has_li = has_li or ("linkedin.com/in/" in html.lower())
        has_gh = bool(
            (github.get("login") and f"github.com/{github['login']}".lower() in html.lower())
            or (gh_url and gh_url.lower() in html.lower())
        )
        if has_li and has_gh:
            ev.append(f"Personal site {site} lists both LinkedIn and GitHub")
    return ev


def _employer_match_evidence(linkedin: dict, github: dict) -> List[str]:
    li_co = _norm_org(linkedin.get("company"))
    # Also parse company-ish tokens from LinkedIn headline
    li_bits = {_norm_org(linkedin.get("company"))}
    headline = linkedin.get("headline") or ""
    for part in re.split(r"[|@·•\-–—]", headline):
        n = _norm_org(part)
        if n and len(n) > 2:
            li_bits.add(n)
    gh_cos = {_norm_org(github.get("company"))}
    for org in github.get("organizations") or []:
        gh_cos.add(_norm_org(org if isinstance(org, str) else str(org)))
    li_bits = {x for x in li_bits if x}
    gh_cos = {x for x in gh_cos if x}
    if not li_bits or not gh_cos:
        return []
    for a in li_bits:
        for b in gh_cos:
            if a == b or (len(a) >= 4 and a in b) or (len(b) >= 4 and b in a):
                return [f"Employer overlap: LinkedIn/company~{a!r} vs GitHub~{b!r}"]
    return []


def _location_match_evidence(linkedin: dict, github: dict) -> List[str]:
    a = _loc_tokens(linkedin.get("location"))
    b = _loc_tokens(github.get("location"))
    if not a or not b:
        return []
    overlap = a & b
    # Require a meaningful token (city/region), not just "united"/"states"
    weak = {"united", "states", "usa", "us", "uk", "area", "remote", "earth", "world"}
    strong = overlap - weak
    if strong:
        return [f"Location overlap: {sorted(strong)}"]
    return []


def _username_email_evidence(
    linkedin: dict, github: dict, *, known_email: Optional[str]
) -> List[str]:
    ev = []
    login = (github.get("login") or "").lower()
    slug = (linkedin_slug(linkedin.get("url")) or "").lower().replace("-", "")
    if login and slug and (login.replace("-", "") in slug or slug in login.replace("-", "")):
        ev.append(f"GitHub login @{login} overlaps LinkedIn slug")
    gh_email = (github.get("email") or "").strip().lower()
    if known_email and gh_email and known_email == gh_email:
        ev.append(f"GitHub public email matches known email ({gh_email})")
    if known_email and "@" in known_email:
        domain = known_email.split("@", 1)[1]
        # company-ish domain match against GH blog host or email domain
        if gh_email and gh_email.endswith("@" + domain):
            ev.append(f"GitHub email shares domain {domain}")
        blog = github.get("blog") or ""
        host = urlparse(blog).netloc.lower() if blog else ""
        if domain and host and domain in host:
            ev.append(f"GitHub blog host relates to email domain {domain}")
    return ev


def _bio_niche_evidence(linkedin: dict, github: dict) -> List[str]:
    li_text = " ".join(
        str(x)
        for x in [
            linkedin.get("headline"),
            linkedin.get("about"),
            " ".join(linkedin.get("featured_titles") or []),
        ]
        if x
    )
    gh_text = " ".join(
        str(x)
        for x in [
            github.get("bio"),
            " ".join(
                (r.get("name") or "") + " " + (r.get("description") or "")
                for r in (github.get("repos") or [])
                if isinstance(r, dict)
            ),
            " ".join(
                " ".join(r.get("topics") or [])
                for r in (github.get("repos") or [])
                if isinstance(r, dict)
            ),
        ]
        if x
    )
    a = _niche_tokens(li_text)
    b = _niche_tokens(gh_text)
    if not a or not b:
        return []
    overlap = a & b
    if len(overlap) >= 2:
        return [f"Bio/niche token overlap: {sorted(list(overlap))[:8]}"]
    # Shared exact project/repo name appearing on LinkedIn about
    for r in github.get("repos") or []:
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip()
        if len(name) >= 4 and name.lower() in li_text.lower():
            return [f"Shared project name on LinkedIn text: {name}"]
    return []


def _is_name_only_pair(linkedin: dict, github: dict, signals: list) -> bool:
    """True when display names match-ish but no corroborating signals fired."""
    if signals:
        return False
    a = (linkedin.get("name") or "").strip().lower()
    b = (github.get("name") or github.get("login") or "").strip().lower()
    if not a or not b:
        return True
    return True  # zero signals ⇒ name-only (or weaker)


# ── misc utils ──────────────────────────────────────────────────────────────


def _uniq(items: Iterable[Optional[str]]) -> List[str]:
    out = []
    seen = set()
    for x in items:
        if not x:
            continue
        k = x.strip().rstrip("/").lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x.strip())
    return out


def _extract_github_urls(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/?", text, flags=re.I)


def _extract_linkedin_urls(text: str) -> List[str]:
    if not text:
        return []
    found = re.findall(
        r"https?://(?:[a-z]+\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?",
        text,
        flags=re.I,
    )
    return [normalize_linkedin_url(u) or u for u in found]


def _github_login_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"github\.com/([A-Za-z0-9_.-]+)", url, flags=re.I)
    if not m:
        # bare username
        if re.fullmatch(r"[A-Za-z0-9_.-]+", url.strip()):
            return url.strip().lstrip("@")
        return None
    login = m.group(1)
    if login.lower() in ("features", "topics", "settings", "orgs", "marketplace"):
        return None
    return login


def _clean_gh_company(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("@"):
        s = s[1:]
    return s or None


def _norm_org(raw: Optional[str]) -> str:
    if not raw:
        return ""
    s = raw.lower().strip()
    s = s.lstrip("@")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    stop = {"inc", "llc", "ltd", "corp", "corporation", "the", "co", "company"}
    toks = [t for t in s.split() if t not in stop]
    return " ".join(toks)


def _loc_tokens(raw: Optional[str]) -> set:
    if not raw:
        return set()
    return {t for t in re.findall(r"[a-z]{3,}", raw.lower()) if t}


def _niche_tokens(raw: str) -> set:
    stop = {
        "with",
        "from",
        "that",
        "this",
        "have",
        "been",
        "were",
        "their",
        "about",
        "software",
        "engineer",
        "developer",
        "student",
        "university",
        "https",
        "http",
        "www",
        "com",
        "working",
        "experience",
    }
    return {t for t in re.findall(r"[a-z][a-z0-9+#.]{2,}", (raw or "").lower()) if t not in stop}


def _looks_like_personal_site(url: str) -> bool:
    try:
        host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    blocked = ("linkedin.com", "github.com", "twitter.com", "x.com", "facebook.com", "instagram.com")
    return not any(b in host for b in blocked)


def _fetch_text(url: str, *, timeout: float = 8.0) -> str:
    try:
        u = url if "://" in url else "https://" + url
        resp = requests.get(
            u,
            timeout=timeout,
            headers={"User-Agent": "ConnectDeeplyBot/0.1 (+identity-resolve)"},
            allow_redirects=True,
        )
        if resp.status_code >= 400:
            return ""
        return resp.text[:200_000]
    except Exception:
        return ""
