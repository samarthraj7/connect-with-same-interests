"""Connect Deeply HTTP API — wraps research + common-ground for the mobile app.

Run from backend/:
  uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.auth import create_token, hash_password, require_user, verify_password
from api.users import UserStore
from common_ground import (
    TOKEN_COST_BASIC,
    TOKEN_COST_DETAILED,
    analyze_common_ground,
    apply_overlap_to_summary,
    public_conversation,
)
from merge import merge_profile
from orchestrator import PersonQuery, SearchOrchestrator
from storage import SOCIAL_SOURCES, ProfileStore
from synthesize import summarize_profile
from user_profile import merge_manual_overlays, profile_from_research

load_dotenv()

app = FastAPI(title="Connect Deeply", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

users = UserStore()
profiles = ProfileStore()
orchestrator = SearchOrchestrator()

# In-memory job progress for long research runs (MVP).
_jobs: dict[str, dict[str, Any]] = {}


# ─── models ─────────────────────────────────────────────────────────────────


class SignupBody(BaseModel):
    email: str
    password: str = Field(min_length=6)
    name: str
    company: str = ""
    university: str = ""
    place: str = ""
    headline: str = ""
    location: str = ""
    linkedin_url: str = ""
    phone: str = ""
    instagram_handle: str = ""
    twitter_handle: str = ""
    facebook_url: str = ""
    github_username: str = ""
    website: str = ""
    # Optional extras layered onto the researched profile
    hobbies: list[str] = []
    interests: list[str] = []
    sports: list[str] = []
    education: list[str] = []
    career_highlights: list[str] = []
    causes_and_affiliations: list[str] = []
    talking_goals: list[str] = []
    research_me: bool = True


class SelfResearchBody(BaseModel):
    company: Optional[str] = None
    university: Optional[str] = None
    place: Optional[str] = None
    linkedin_url: Optional[str] = None
    force_refresh: bool = True


class LoginBody(BaseModel):
    email: str
    password: str


class ProfileUpdateBody(BaseModel):
    name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    hometown_or_raised: Optional[str] = None
    hobbies: Optional[list[str]] = None
    interests: Optional[list[str]] = None
    sports: Optional[list[str]] = None
    education: Optional[list[str]] = None
    career_highlights: Optional[list[str]] = None
    causes_and_affiliations: Optional[list[str]] = None
    talking_goals: Optional[list[str]] = None
    avoid_topics: Optional[list[str]] = None
    linkedin_url: Optional[str] = None
    phone: Optional[str] = None


class ResearchBody(BaseModel):
    name: str
    company: Optional[str] = None
    university: Optional[str] = None
    place: Optional[str] = None
    linkedin_url: Optional[str] = None
    tier: Literal["basic", "detailed"] = "detailed"
    fetch_social: bool = False
    force_refresh: bool = False


class InteractionBody(BaseModel):
    type: str = "note"
    note: str = ""
    meta: dict[str, Any] = {}


class CandidatesBody(BaseModel):
    name: str


# ─── auth ───────────────────────────────────────────────────────────────────


@app.post("/auth/signup")
def signup(body: SignupBody):
    if users.find_by_email(body.email):
        raise HTTPException(400, "Email already registered")

    email = body.email.strip().lower()
    name = body.name.strip()
    company = (body.company or "").strip() or None
    university = (body.university or "").strip() or None
    place = (body.place or body.location or "").strip() or None
    linkedin_url = (body.linkedin_url or "").strip() or None
    instagram = (body.instagram_handle or "").strip().lstrip("@")
    twitter = (body.twitter_handle or "").strip().lstrip("@")
    github = (body.github_username or "").strip()
    facebook = (body.facebook_url or "").strip()
    phone = (body.phone or "").strip()
    website = (body.website or "").strip()

    seed = {
        "name": name,
        "headline": body.headline.strip(),
        "location": (body.location or body.place or "").strip(),
        "current_company": (body.company or "").strip(),
        "hobbies": _clean_list(body.hobbies),
        "interests": _clean_list(body.interests),
        "sports": _clean_list(body.sports),
        "education": _clean_list(body.education),
        "career_highlights": _clean_list(body.career_highlights),
        "causes_and_affiliations": _clean_list(body.causes_and_affiliations),
        "talking_goals": _clean_list(body.talking_goals)
        or [
            "Find genuine common ground before meetings",
            "Open warm, specific conversations",
        ],
        "avoid_topics": [],
        "contact": {
            "email": email,
            "phone": phone,
            "linkedin_url": linkedin_url or "",
            "instagram_handle": instagram,
            "twitter_handle": twitter,
            "facebook_url": facebook,
            "github_username": github,
            "website": website,
        },
        "signup_form": {
            "company": company,
            "university": university,
            "place": place,
            "headline": body.headline.strip(),
            "location": (body.location or body.place or "").strip(),
            "hobbies": _clean_list(body.hobbies),
            "interests": _clean_list(body.interests),
            "sports": _clean_list(body.sports),
            "socials": {
                "linkedin_url": linkedin_url,
                "instagram_handle": instagram,
                "twitter_handle": twitter,
                "facebook_url": facebook,
                "github_username": github,
                "website": website,
                "phone": phone,
            },
        },
        "profile_source": "signup_seed",
        "research_status": "pending" if body.research_me else "skipped",
    }

    print(f"\n[signup] creating account for {email} ({name})")
    user = users.create(
        email=email,
        password_hash=hash_password(body.password),
        profile=seed,
        starting_tokens=int(os.environ.get("STARTING_TOKENS", "15")),
    )
    print(f"[signup] saved user JSON → users/{user['id']}.json")

    research_meta: dict[str, Any] = {"attempted": False}
    if body.research_me:
        research_meta["attempted"] = True
        fetch_social = bool(instagram or twitter or facebook)
        print(
            f"[signup] researching YOU publicly "
            f"(company={company}, university={university}, social={fetch_social})…"
        )
        briefing = _fetch_person_briefing(
            name=name,
            company=company,
            university=university,
            place=place,
            linkedin_url=linkedin_url,
            fetch_social=fetch_social,
            force_refresh=True,
            github_username=github or None,
            instagram_handle=instagram or None,
            twitter_handle=twitter or None,
            facebook_url=facebook or None,
        )
        research_meta["status"] = briefing.get("status")
        if briefing.get("status") == "ok":
            researched = profile_from_research(
                name=name,
                company=company,
                summary=briefing["summary"],
                sources=briefing.get("sources") or {},
                contact=seed["contact"],
            )
            researched["researched_at"] = datetime.now(timezone.utc).isoformat()
            researched["research_status"] = "ok"
            researched["self_profile_slug"] = briefing.get("profile_slug")
            researched["signup_form"] = seed["signup_form"]
            merged = merge_manual_overlays(researched, seed)
            contact = dict(merged.get("contact") or {})
            contact.update({k: v for k, v in seed["contact"].items() if v})
            merged["contact"] = contact
            user = users.replace_profile(user["id"], merged)
            research_meta["profile_slug"] = briefing.get("profile_slug")
            print(f"[signup] self-research OK → profile slug {briefing.get('profile_slug')}")
        else:
            seed["research_status"] = "failed"
            seed["research_error"] = briefing.get("error") or briefing.get("reason")
            user = users.replace_profile(user["id"], seed)
            research_meta["error"] = seed.get("research_error")
            print(f"[signup] self-research failed: {research_meta['error']}")

    token = create_token(user["id"], user["email"])
    return {
        "token": token,
        "user": _public_user(user),
        "self_research": research_meta,
    }


@app.post("/me/research")
def research_me(body: SelfResearchBody, user=Depends(require_user)):
    """Re-run public research on the signed-in user and refresh their YOU profile."""
    profile = user.get("profile") or {}
    name = profile.get("name") or ""
    if not name:
        raise HTTPException(400, "Your profile needs a name before we can research you")

    company = (body.company or profile.get("current_company") or "").strip() or None
    university = (body.university or "").strip() or None
    place = (body.place or profile.get("location") or "").strip() or None
    linkedin_url = (
        body.linkedin_url
        or (profile.get("contact") or {}).get("linkedin_url")
        or ""
    ).strip() or None

    briefing = _fetch_person_briefing(
        name=name,
        company=company,
        university=university,
        place=place,
        linkedin_url=linkedin_url,
        fetch_social=False,
        force_refresh=body.force_refresh,
    )
    if briefing.get("status") != "ok":
        raise HTTPException(502, briefing.get("error") or "Self-research failed")

    researched = profile_from_research(
        name=name,
        company=company,
        summary=briefing["summary"],
        sources=briefing.get("sources") or {},
        contact=profile.get("contact") or {},
    )
    researched["researched_at"] = datetime.now(timezone.utc).isoformat()
    researched["research_status"] = "ok"
    researched["self_profile_slug"] = briefing.get("profile_slug")
    # Preserve manual hobbies/interests the user already edited
    merged = merge_manual_overlays(researched, profile)
    user = users.replace_profile(user["id"], merged)
    return {
        "status": "ok",
        "user": _public_user(user),
        "profile_slug": briefing.get("profile_slug"),
    }


@app.post("/auth/login")
def login(body: LoginBody):
    user = users.find_by_email(body.email.strip().lower())
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    token = create_token(user["id"], user["email"])
    return {"token": token, "user": _public_user(user)}


@app.get("/me")
def me(user=Depends(require_user)):
    return _public_user(user)


@app.patch("/me/profile")
def update_profile(body: ProfileUpdateBody, user=Depends(require_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    contact_keys = {}
    if "linkedin_url" in updates:
        contact_keys["linkedin_url"] = updates.pop("linkedin_url")
    if "phone" in updates:
        contact_keys["phone"] = updates.pop("phone")
    if contact_keys:
        contact = dict(user.get("profile", {}).get("contact") or {})
        contact.update(contact_keys)
        updates["contact"] = contact
    # Keep refining signup social signal
    if any(k in updates for k in ("hobbies", "interests", "sports")):
        updates["social_from_signup"] = True
        updates["profile_refined_at"] = datetime.now(timezone.utc).isoformat()
    user = users.update_profile(user["id"], updates)
    return _public_user(user)


# ─── research ───────────────────────────────────────────────────────────────


@app.post("/candidates")
def candidates(body: CandidatesBody, user=Depends(require_user)):
    from connectors import gemini_search

    result = gemini_search.find_candidates(body.name.strip())
    if result.get("status") != "ok":
        return {"candidates": [], "status": result.get("status"), "error": result.get("error")}
    return {"candidates": result.get("candidates") or [], "status": "ok"}


@app.post("/research")
def research(body: ResearchBody, user=Depends(require_user)):
    cost = TOKEN_COST_DETAILED if body.tier == "detailed" else TOKEN_COST_BASIC
    if user.get("tokens", 0) < cost:
        raise HTTPException(
            402,
            f"Not enough tokens (need {cost}, have {user.get('tokens', 0)}). "
            "Basic=1, detailed (with conversation ideas)=3.",
        )

    result = _run_research(body, user)
    if result.get("status") == "ok":
        users.charge_tokens(user["id"], cost, reason=f"research:{body.tier}:{body.name}")
        users.append_interaction(
            user["id"],
            {
                "type": "research",
                "person_name": body.name,
                "company": body.company,
                "tier": body.tier,
                "tokens_charged": cost,
                "profile_slug": result.get("profile_slug"),
            },
        )
        user = users.get(user["id"])
        result["tokens_remaining"] = user.get("tokens")
    # Never send internal overlap engine fields to the client.
    result.pop("_engine", None)
    return result


@app.get("/people")
def list_people(user=Depends(require_user)):
    """CRM list: people this user has researched (from interaction history)."""
    seen = {}
    for event in reversed(user.get("interactions") or []):
        if event.get("type") != "research":
            continue
        key = (event.get("person_name") or "", event.get("company") or "")
        if key in seen or not key[0]:
            continue
        slug = event.get("profile_slug")
        record = profiles.load(key[0], key[1] or None) if key[0] else None
        seen[key] = {
            "name": key[0],
            "company": key[1] or None,
            "profile_slug": slug,
            "last_researched_at": event.get("at"),
            "tier": event.get("tier"),
            "has_conversation": event.get("tier") == "detailed",
            "contact": (record or {}).get("contact") or {},
            "summary_blurb": ((record or {}).get("latest_summary") or {}).get("summary"),
            "talk_teaser": _talk_teaser(record),
        }
    return {"people": list(seen.values())}


@app.get("/people/{name}")
def get_person(name: str, company: Optional[str] = None, user=Depends(require_user)):
    record = profiles.load(name, company)
    if not record:
        raise HTTPException(404, "Person not found in your research cache")
    sources = record.get("latest_sources") or {}
    engine = record.get("latest_common_ground")
    summary = record.get("latest_summary") or {}
    conversation = public_conversation(engine) if engine else public_conversation(
        summary.get("conversation") or summary.get("common_ground")
    )
    return {
        "name": record.get("name"),
        "company": record.get("company"),
        "contact": record.get("contact") or {},
        "summary": _public_summary(summary),
        "conversation": conversation,
        "usage": {
            "tier": (record.get("latest_usage") or {}).get("tier"),
            "tokens_charged": (record.get("latest_usage") or {}).get("tokens_charged"),
        },
        "interactions": record.get("interactions") or [],
        "updated_at": record.get("updated_at"),
        "source_status": record.get("latest_source_status") or {},
        "sources": {
            "personal_info": sources.get("personal_info"),
            "instagram_public": sources.get("instagram_public"),
            "facebook_public": sources.get("facebook_public"),
            "twitter_public": sources.get("twitter_public"),
            "linkedin_public": sources.get("linkedin_public"),
            "github": sources.get("github"),
            "exa_search": {
                "linkedin_url": (sources.get("exa_search") or {}).get("linkedin_url"),
                "status": (sources.get("exa_search") or {}).get("status"),
            }
            if sources.get("exa_search")
            else None,
        },
    }


@app.post("/people/{name}/interactions")
def add_interaction(
    name: str,
    body: InteractionBody,
    company: Optional[str] = None,
    user=Depends(require_user),
):
    profiles.record_interaction(
        name,
        company,
        {"type": body.type, "note": body.note, "meta": body.meta, "user_id": user["id"]},
    )
    users.append_interaction(
        user["id"],
        {"type": body.type, "person_name": name, "company": company, "note": body.note, **body.meta},
    )
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "service": "connect-deeply"}


# ─── internals ──────────────────────────────────────────────────────────────


def _fetch_person_briefing(
    *,
    name: str,
    company: Optional[str] = None,
    university: Optional[str] = None,
    place: Optional[str] = None,
    linkedin_url: Optional[str] = None,
    fetch_social: bool = False,
    force_refresh: bool = False,
    github_username: Optional[str] = None,
    instagram_handle: Optional[str] = None,
    twitter_handle: Optional[str] = None,
    facebook_url: Optional[str] = None,
) -> dict[str, Any]:
    """Research + summarize a person (no conversation / no token charge)."""
    ttl_hours = float(os.environ.get("CACHE_TTL_HOURS", "24"))
    if force_refresh:
        fresh: set = set()
    else:
        fresh, _ = profiles.freshness(name, company, ttl_hours)

    skip = set(fresh)
    if not fetch_social:
        skip |= set(SOCIAL_SOURCES)

    existing = profiles.load(name, company)
    raw_results = orchestrator.run(
        PersonQuery(
            name=name,
            company=company,
            university=university,
            place=place,
            linkedin_url=linkedin_url,
            github_username=github_username,
        ),
        skip=frozenset(skip),
    )

    # If signup provided exact social handles, deep-fetch those directly.
    if instagram_handle and "instagram_public" not in skip:
        from connectors import instagram as ig

        print(f"  [self] Instagram @{instagram_handle}")
        raw_results["instagram_public"] = ig.fetch_instagram(
            name=name,
            company=company,
            university=university,
            place=place,
            instagram_url=f"https://www.instagram.com/{instagram_handle}/",
        )
    if twitter_handle and "twitter_public" not in skip:
        from connectors import twitter as tw

        print(f"  [self] Twitter @{twitter_handle}")
        raw_results["twitter_public"] = tw.fetch_twitter(
            name=name,
            company=company,
            university=university,
            place=place,
            twitter_url=f"https://x.com/{twitter_handle}",
        )
    if facebook_url and "facebook_public" not in skip:
        from connectors import facebook as fb

        print(f"  [self] Facebook {facebook_url}")
        raw_results["facebook_public"] = fb.fetch_facebook(
            name=name,
            company=company,
            university=university,
            place=place,
            facebook_url=facebook_url,
        )

    merged = merge_profile(
        query={
            "name": name,
            "company": company,
            "university": university,
            "place": place,
            "linkedin_url": linkedin_url,
            "github_username": github_username,
        },
        raw_results=raw_results,
    )

    has_new_data = any(r.get("status") == "ok" for r in raw_results.values())
    if not has_new_data and existing and existing.get("latest_summary"):
        summary = existing["latest_summary"]
    else:
        cached_sources = (existing or {}).get("latest_sources", {})
        llm_view = copy.deepcopy(merged)
        llm_view["sources"] = {**cached_sources, **raw_results}
        summary = summarize_profile(llm_view)
        if summary.get("status") != "ok":
            cached = (existing or {}).get("latest_summary")
            if isinstance(cached, dict) and cached.get("status") == "ok":
                summary = cached
            else:
                return {
                    "status": "error",
                    "error": summary.get("error") or "Summarization failed",
                    "source_status": {k: v.get("status") for k, v in raw_results.items()},
                }

    path = profiles.save(name, company, merged, summary, common_ground=None, usage={"tier": "self"})
    cached_sources = (existing or {}).get("latest_sources", {})
    return {
        "status": "ok",
        "name": name,
        "company": company,
        "summary": summary,
        "sources": {**cached_sources, **raw_results},
        "profile_path": str(path),
        "profile_slug": path.stem,
    }


def _run_research(body: ResearchBody, user: dict) -> dict:
    name = body.name.strip()
    company = (body.company or "").strip() or None
    ttl_hours = float(os.environ.get("CACHE_TTL_HOURS", "24"))

    if body.force_refresh:
        fresh: set = set()
    else:
        fresh, _ = profiles.freshness(name, company, ttl_hours)

    skip = set(fresh)
    if not body.fetch_social:
        skip |= set(SOCIAL_SOURCES)

    existing = profiles.load(name, company)
    raw_results = orchestrator.run(
        PersonQuery(
            name=name,
            company=company,
            university=body.university,
            place=body.place,
            linkedin_url=body.linkedin_url,
        ),
        skip=frozenset(skip),
    )

    merged = merge_profile(
        query={
            "name": name,
            "company": company,
            "university": body.university,
            "place": body.place,
            "linkedin_url": body.linkedin_url,
        },
        raw_results=raw_results,
    )

    has_new_data = any(r.get("status") == "ok" for r in raw_results.values())
    if not has_new_data and existing and existing.get("latest_summary"):
        summary = existing["latest_summary"]
    else:
        cached_sources = (existing or {}).get("latest_sources", {})
        llm_view = copy.deepcopy(merged)
        llm_view["sources"] = {**cached_sources, **raw_results}
        summary = summarize_profile(llm_view)
        if summary.get("status") != "ok":
            cached = (existing or {}).get("latest_summary")
            if isinstance(cached, dict) and cached.get("status") == "ok":
                summary = cached
            else:
                return {
                    "status": "error",
                    "error": summary.get("error") or "Summarization failed",
                    "source_status": {k: v.get("status") for k, v in raw_results.items()},
                }

    cached_sources = (existing or {}).get("latest_sources", {})
    all_sources = {**cached_sources, **raw_results}

    overlap = None
    usage = {"tier": "basic", "tokens_charged": TOKEN_COST_BASIC}
    if body.tier == "detailed" and summary.get("status") == "ok":
        you_profile = user.get("profile") or {}
        overlap = analyze_common_ground(
            summary,
            them_name=name,
            user_profile=you_profile,
            them_sources=all_sources,
        )
        if overlap.get("status") == "ok":
            summary = apply_overlap_to_summary(summary, overlap)
            usage = {
                "tier": "detailed",
                "tokens_charged": TOKEN_COST_DETAILED,
            }
            # Feed profile-gap suggestions back onto the user for Phase-3 refinement.
            gaps = overlap.get("your_profile_gaps") or []
            if gaps:
                users.update_profile(
                    user["id"],
                    {"profile_refinement": {"known_gaps": gaps, "last_from": name}},
                )

    path = profiles.save(
        name, company, merged, summary, common_ground=overlap, usage=usage
    )
    profiles.record_interaction(
        name,
        company,
        {
            "type": "research",
            "tier": usage.get("tier"),
            "tokens_charged": usage.get("tokens_charged"),
            "user_id": user["id"],
        },
    )

    conversation = public_conversation(overlap) if overlap else {"status": "skipped"}
    return {
        "status": "ok",
        "name": name,
        "company": company,
        "profile_path": str(path),
        "profile_slug": path.stem,
        "summary": _public_summary(summary),
        "conversation": conversation,
        "_engine": overlap,  # not for clients; useful for server-side logging
        "contact": (profiles.load(name, company) or {}).get("contact") or {},
        "usage": usage,
        "source_status": {
            **{k: v.get("status") for k, v in raw_results.items()},
            **{s: "skipped" for s in SOCIAL_SOURCES if not body.fetch_social},
        },
    }


def _public_user(user: dict) -> dict:
    profile = user.get("profile") or {}
    return {
        "id": user["id"],
        "email": user["email"],
        "tokens": user.get("tokens", 0),
        "created_at": user.get("created_at"),
        "profile": profile,
        "profile_source": profile.get("profile_source"),
        "research_status": profile.get("research_status"),
        "profile_refinement": profile.get("profile_refinement") or user.get("profile_refinement"),
        "interaction_count": len(user.get("interactions") or []),
    }


def _public_summary(summary: Optional[dict]) -> dict:
    """Drop internal engine fields before sending a briefing to the app."""
    if not isinstance(summary, dict):
        return {}
    skip = {"_conversation_engine", "common_ground", "raw_text"}
    return {k: v for k, v in summary.items() if k not in skip}


def _talk_teaser(record: Optional[dict]) -> Optional[str]:
    if not record:
        return None
    conv = public_conversation(record.get("latest_common_ground"))
    topics = conv.get("talk_about") or []
    if topics and isinstance(topics[0], dict) and topics[0].get("topic"):
        return topics[0]["topic"]
    openers = conv.get("openers") or []
    return openers[0] if openers else None


def _clean_list(items: list[str]) -> list[str]:
    out = []
    for item in items or []:
        s = (item or "").strip()
        if s and s not in out:
            out.append(s)
    return out
