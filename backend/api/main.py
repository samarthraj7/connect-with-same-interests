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
)
from merge import merge_profile
from orchestrator import PersonQuery, SearchOrchestrator
from storage import SOCIAL_SOURCES, ProfileStore
from synthesize import summarize_profile

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
    headline: str = ""
    location: str = ""
    hobbies: list[str] = []
    interests: list[str] = []
    sports: list[str] = []
    education: list[str] = []
    career_highlights: list[str] = []
    causes_and_affiliations: list[str] = []
    talking_goals: list[str] = []
    linkedin_url: str = ""


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
    profile = {
        "name": body.name.strip(),
        "headline": body.headline.strip(),
        "location": body.location.strip(),
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
            "email": body.email.strip().lower(),
            "linkedin_url": body.linkedin_url.strip(),
            "phone": "",
        },
        # Signup hobbies/interests are first-class social signal for overlap.
        "social_from_signup": True,
    }
    user = users.create(
        email=body.email.strip().lower(),
        password_hash=hash_password(body.password),
        profile=profile,
        starting_tokens=int(os.environ.get("STARTING_TOKENS", "15")),
    )
    token = create_token(user["id"], user["email"])
    return {"token": token, "user": _public_user(user)}


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
            "Basic=1, detailed with overlap=3.",
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
                "overlap_score": (result.get("common_ground") or {}).get("overlap_score"),
                "profile_slug": result.get("profile_slug"),
            },
        )
        user = users.get(user["id"])
        result["tokens_remaining"] = user.get("tokens")
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
            "overlap_score": event.get("overlap_score"),
            "tier": event.get("tier"),
            "contact": (record or {}).get("contact") or {},
            "summary_blurb": ((record or {}).get("latest_summary") or {}).get("summary"),
        }
    return {"people": list(seen.values())}


@app.get("/people/{name}")
def get_person(name: str, company: Optional[str] = None, user=Depends(require_user)):
    record = profiles.load(name, company)
    if not record:
        raise HTTPException(404, "Person not found in your research cache")
    sources = record.get("latest_sources") or {}
    return {
        "name": record.get("name"),
        "company": record.get("company"),
        "contact": record.get("contact") or {},
        "summary": record.get("latest_summary"),
        "common_ground": record.get("latest_common_ground"),
        "usage": record.get("latest_usage"),
        "interactions": record.get("interactions") or [],
        "updated_at": record.get("updated_at"),
        "source_status": record.get("latest_source_status") or {},
        # Lightweight social / personal snapshots for the full briefing UI
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
                "overlap_score": overlap.get("overlap_score"),
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
            "overlap_score": usage.get("overlap_score"),
            "user_id": user["id"],
        },
    )

    return {
        "status": "ok",
        "name": name,
        "company": company,
        "profile_path": str(path),
        "profile_slug": path.stem,
        "summary": summary,
        "common_ground": overlap,
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
        "profile_refinement": profile.get("profile_refinement") or user.get("profile_refinement"),
        "interaction_count": len(user.get("interactions") or []),
    }


def _clean_list(items: list[str]) -> list[str]:
    out = []
    for item in items or []:
        s = (item or "").strip()
        if s and s not in out:
            out.append(s)
    return out
