"""Connect Deeply HTTP API — wraps research + common-ground for the mobile app.

Run from backend/:
  uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import copy
import os
import threading
import uuid
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
    # Pre-auth research draft from /public/research/start
    draft_id: Optional[str] = None
    # From POST /auth/signup/otp/verify
    email_verified_token: Optional[str] = None


class SignupOtpSendBody(BaseModel):
    email: str


class SignupOtpVerifyBody(BaseModel):
    email: str
    code: str


class PublicResearchBody(BaseModel):
    name: str
    company: Optional[str] = None
    university: Optional[str] = None
    place: Optional[str] = None
    linkedin_url: Optional[str] = None
    force_refresh: bool = True


class ResearchFeedbackBody(BaseModel):
    draft_id: str
    rating: Literal["good", "bad"]
    wrong_notes: Optional[str] = None
    wrong_categories: Optional[list[str]] = None
    # After Bad, immediately re-research using stored corrections (default on).
    auto_retry: bool = True


class PublicResearchFeedbackBody(BaseModel):
    draft_id: str
    rating: Literal["good", "bad"] = "bad"
    wrong_notes: Optional[str] = None
    wrong_categories: Optional[list[str]] = None
    auto_retry: bool = True
    force_refresh: bool = True


class SelfResearchBody(BaseModel):
    company: Optional[str] = None
    university: Optional[str] = None
    place: Optional[str] = None
    linkedin_url: Optional[str] = None
    force_refresh: bool = True
    auto_commit: bool = False


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
    fetch_social: bool = True
    force_refresh: bool = False
    # When false (default), result is a draft until POST /research/feedback rates it good.
    auto_commit: bool = False


class InteractionBody(BaseModel):
    type: str = "note"
    note: str = ""
    meta: dict[str, Any] = {}


class PersonChatBody(BaseModel):
    question: str
    company: Optional[str] = None
    draft_id: Optional[str] = None
    history: Optional[list] = None


class CandidatesBody(BaseModel):
    name: str
    company: Optional[str] = None
    university: Optional[str] = None
    linkedin_url: Optional[str] = None


class JournalEntryBody(BaseModel):
    body: str
    entry_type: str = "note"
    tags: list[str] = []


class VerifyHandlesBody(BaseModel):
    name: str
    company: Optional[str] = None
    university: Optional[str] = None
    linkedin_url: Optional[str] = None
    handles: dict[str, str] = {}


class IdentityResolveBody(BaseModel):
    linkedin_url: str
    github_username: Optional[str] = None
    github_url: Optional[str] = None
    name: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    known_email: Optional[str] = None


class IdentityQueueDecisionBody(BaseModel):
    decision: Literal["confirm", "reject"]


class ConnectionsUploadBody(BaseModel):
    csv: str
    filename: str = "connections.csv"


class SettingsBody(BaseModel):
    theme: Optional[str] = None
    auto_prep: Optional[bool] = None


class PendingFactBody(BaseModel):
    claim: str
    person_name: Optional[str] = None
    person_company: Optional[str] = None
    trusted_personal: bool = False


class PendingFactUpdateBody(BaseModel):
    status: Literal["pending", "corroborated", "trusted_personal", "rejected"]
    evidence: Optional[dict[str, Any]] = None


class CalendarOAuthBody(BaseModel):
    code: str
    redirect_uri: str


class OtpSendBody(BaseModel):
    channel: Literal["email", "phone"]
    destination: Optional[str] = None


class OtpVerifyBody(BaseModel):
    channel: Literal["email", "phone"]
    code: str


# ─── auth ───────────────────────────────────────────────────────────────────


@app.post("/auth/signup")
def signup(body: SignupBody):
    if users.find_by_email(body.email):
        raise HTTPException(400, "Email already registered")

    from otp import consume_email_verified_token

    email = body.email.strip().lower()
    require_otp = (os.environ.get("SIGNUP_REQUIRE_EMAIL_OTP") or "true").lower() in (
        "1",
        "true",
        "yes",
    )
    if require_otp and not consume_email_verified_token(body.email_verified_token, email):
        raise HTTPException(
            400,
            "Verify your email with the code we sent before creating an account.",
        )

    name = body.name.strip()
    company = (body.company or "").strip() or None
    university = (body.university or "").strip() or None
    place = (body.place or body.location or "").strip() or None
    linkedin_url = (body.linkedin_url or "").strip() or None

    # Prefer pre-auth research draft when present — skip live research_me
    draft = None
    if body.draft_id:
        from research_drafts import load_draft

        draft = load_draft(body.draft_id)
        if not draft:
            raise HTTPException(400, "Research draft not found or expired — run Find Me again")
        name = (draft.get("name") or name).strip()
        company = (draft.get("company") or company or None)
        university = (draft.get("university") or university or None)
        place = (draft.get("place") or place or None)
        linkedin_url = (draft.get("linkedin_url") or linkedin_url or None)

    want_research = bool(body.research_me) and not draft
    if want_research and not (company or university or linkedin_url):
        raise HTTPException(
            400,
            "Company, university, or LinkedIn URL required to research you.",
        )
    instagram = (body.instagram_handle or "").strip().lstrip("@")
    twitter = (body.twitter_handle or "").strip().lstrip("@")
    github = (body.github_username or "").strip()
    facebook = (body.facebook_url or "").strip()
    phone = (body.phone or "").strip()
    website = (body.website or "").strip()

    current_company = (body.company or "").strip() or (company or "") or ""
    seed = {
        "name": name,
        "headline": body.headline.strip(),
        "location": (body.location or body.place or "").strip(),
        "current_company": current_company,
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
        "research_status": "pending" if want_research else ("ok" if draft else "skipped"),
    }

    if draft and draft.get("summary"):
        researched = profile_from_research(
            name=name,
            company=company,
            summary=draft.get("summary") or {},
            sources=draft.get("all_sources") or draft.get("sources") or {},
            contact=seed["contact"],
            linkedin_url=linkedin_url,
        )
        researched["researched_at"] = datetime.now(timezone.utc).isoformat()
        researched["research_status"] = "ok"
        researched["research_draft_id"] = body.draft_id
        researched["profile_source"] = "claimed_public"
        # User edits from signup form win over researched fields when provided
        overlays = {
            "headline": body.headline.strip() or None,
            "hobbies": _clean_list(body.hobbies) or None,
            "interests": _clean_list(body.interests) or None,
            "sports": _clean_list(body.sports) or None,
            "contact": seed["contact"],
        }
        seed = merge_manual_overlays(researched, {k: v for k, v in overlays.items() if v})
        seed["contact"] = {
            **(seed.get("contact") or {}),
            **overlays["contact"],
        }
        if body.headline.strip():
            seed["headline"] = body.headline.strip()
        if _clean_list(body.hobbies):
            seed["hobbies"] = _clean_list(body.hobbies)
        if _clean_list(body.interests):
            seed["interests"] = _clean_list(body.interests)
        if _clean_list(body.sports):
            seed["sports"] = _clean_list(body.sports)


    print(f"\n[signup] creating account for {email} ({name})")
    # Verify optional socials once — only when this request also researches YOU
    # (modal signup verifies once client-side then research_me=false).
    handle_verification: dict[str, Any] = {}
    handles_to_check = {}
    if body.research_me:
        if github:
            handles_to_check["github"] = github
        if linkedin_url:
            handles_to_check["linkedin"] = linkedin_url
        if instagram:
            handles_to_check["instagram"] = instagram
        if twitter:
            handles_to_check["twitter"] = twitter
    if handles_to_check:
        from handle_verify import verify_handles

        try:
            vr = verify_handles(
                name=name,
                company=company,
                university=university,
                linkedin_url=linkedin_url,
                handles=handles_to_check,
            )
            handle_verification = vr.get("results") or {}
            seed["handle_verification"] = handle_verification
            # Only feed verified (or ambiguous LinkedIn URL) into enrichment
            if handle_verification.get("github", {}).get("status") == "rejected":
                github = ""
                seed["contact"]["github_username"] = ""
            if handle_verification.get("instagram", {}).get("status") == "rejected":
                instagram = ""
                seed["contact"]["instagram_handle"] = ""
            if handle_verification.get("twitter", {}).get("status") == "rejected":
                twitter = ""
                seed["contact"]["twitter_handle"] = ""
        except Exception as e:
            print(f"[signup] handle verify skipped: {e}")

    user = users.create(
        email=email,
        password_hash=hash_password(body.password),
        profile=seed,
        starting_tokens=int(os.environ.get("STARTING_TOKENS", "15")),
    )
    print(f"[signup] saved user JSON → users/{user['id']}.json")

    research_meta: dict[str, Any] = {"attempted": False, "from_draft": bool(draft)}
    if draft:
        research_meta["attempted"] = True
        research_meta["status"] = "ok"
        research_meta["draft_id"] = body.draft_id
        try:
            from research_drafts import delete_draft

            if body.draft_id:
                delete_draft(body.draft_id)
        except Exception:
            pass
    elif want_research:
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
                linkedin_url=linkedin_url,
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


def _run_self_research(
    user: dict,
    body: SelfResearchBody,
    *,
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """Shared self-research path used by sync POST /me/research and async jobs."""
    profile = user.get("profile") or {}
    name = profile.get("name") or ""
    if not name:
        return {"status": "error", "error": "Your profile needs a name before we can research you"}

    company = (body.company or profile.get("current_company") or "").strip() or None
    university = (body.university or "").strip() or None
    place = (body.place or profile.get("location") or "").strip() or None
    linkedin_url = (
        body.linkedin_url
        or (profile.get("contact") or {}).get("linkedin_url")
        or ""
    ).strip() or None

    if on_progress:
        try:
            on_progress("queued", 0.02, "Starting research…")
        except Exception:
            pass

    briefing = _fetch_person_briefing(
        name=name,
        company=company,
        university=university,
        place=place,
        linkedin_url=linkedin_url,
        fetch_social=True,
        force_refresh=body.force_refresh,
        persist=body.auto_commit,
        user_id=user["id"],
        on_progress=on_progress,
    )

    if briefing.get("status") != "ok":
        err = briefing.get("error") or "Self-research failed"
        try:
            failed_profile = dict(user.get("profile") or {})
            failed_profile["research_status"] = "failed"
            failed_profile["research_error"] = err
            users.replace_profile(user["id"], failed_profile)
        except Exception:
            pass
        return {"status": "error", "error": err}

    if briefing.get("needs_rating") and briefing.get("draft_id"):
        pending_profile = dict(user.get("profile") or {})
        pending_profile["research_status"] = "pending_rating"
        pending_profile["research_draft_id"] = briefing["draft_id"]
        user = users.replace_profile(user["id"], pending_profile)
        out = {
            "status": "ok",
            "needs_rating": True,
            "draft_id": briefing["draft_id"],
            "summary": _public_summary(briefing.get("summary") or {}),
            "user": _public_user(user),
            "name": name,
            "company": company,
        }
        if on_progress:
            try:
                on_progress("done", 1.0, "Research ready for your review")
            except Exception:
                pass
        return out

    researched = profile_from_research(
        name=name,
        company=company,
        summary=briefing["summary"],
        sources=briefing.get("sources") or {},
        contact=profile.get("contact") or {},
        linkedin_url=linkedin_url,
    )
    researched["researched_at"] = datetime.now(timezone.utc).isoformat()
    researched["research_status"] = "ok"
    researched["self_profile_slug"] = briefing.get("profile_slug")
    researched["profile_source"] = "claimed_public"
    merged = merge_manual_overlays(researched, profile)
    user = users.replace_profile(user["id"], merged)

    if briefing.get("profile_slug"):
        rec = profiles.load(name, company) or {}
        if rec:
            rec["claimed_user_id"] = user["id"]
            rec["visibility"] = "public"
            if university:
                rec["university"] = university
            profiles.path_for(name, company).write_text(
                __import__("json").dumps(rec, indent=2)
            )

    out = {
        "status": "ok",
        "needs_rating": False,
        "draft_id": None,
        "user": _public_user(user),
        "profile_slug": briefing.get("profile_slug"),
        "summary": _public_summary(briefing.get("summary") or {}),
        "visibility": {"public_dossier": True, "private_journal": True},
        "name": name,
        "company": company,
    }
    if on_progress:
        try:
            on_progress("done", 1.0, "Research complete")
        except Exception:
            pass
    return out


def _job_update(job_id: str, **fields: Any) -> None:
    job = _jobs.get(job_id) or {}
    job.update(fields)
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    _jobs[job_id] = job


@app.post("/me/research")
def research_me(body: SelfResearchBody, user=Depends(require_user)):
    """Re-run public research on the signed-in user. Defaults to draft until rated good."""
    result = _run_self_research(user, body)
    if result.get("status") != "ok":
        raise HTTPException(502, result.get("error") or "Self-research failed")
    return result


@app.post("/me/research/start")
def research_me_start(body: SelfResearchBody, user=Depends(require_user)):
    """Start self-research in the background; poll GET /me/research/jobs/{job_id}."""
    profile = user.get("profile") or {}
    if not (profile.get("name") or "").strip():
        raise HTTPException(400, "Your profile needs a name before we can research you")

    job_id = uuid.uuid4().hex
    user_id = user["id"]
    _job_update(
        job_id,
        status="running",
        stage="queued",
        progress=0.01,
        message="Queued…",
        user_id=user_id,
        kind="self_research",
        result=None,
        error=None,
    )

    def worker() -> None:
        def on_progress(stage: str, progress: float, message: str) -> None:
            _job_update(
                job_id,
                status="running",
                stage=stage,
                progress=max(0.0, min(0.99, float(progress))),
                message=message,
            )

        try:
            fresh = users.get(user_id) or user
            result = _run_self_research(fresh, body, on_progress=on_progress)
            if result.get("status") != "ok":
                _job_update(
                    job_id,
                    status="error",
                    stage="error",
                    progress=1.0,
                    message=result.get("error") or "Self-research failed",
                    error=result.get("error") or "Self-research failed",
                    result=None,
                )
                return
            _job_update(
                job_id,
                status="done",
                stage="done",
                progress=1.0,
                message="Research ready for your review",
                result=result,
                error=None,
            )
        except Exception as exc:
            _job_update(
                job_id,
                status="error",
                stage="error",
                progress=1.0,
                message=str(exc)[:300],
                error=str(exc)[:300],
                result=None,
            )

    threading.Thread(target=worker, daemon=True).start()
    return {"status": "ok", "job_id": job_id}


@app.get("/me/research/jobs/{job_id}")
def research_me_job(job_id: str, user=Depends(require_user)):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("user_id") and job["user_id"] != user["id"]:
        raise HTTPException(403, "Not your research job")
    return {
        "status": job.get("status") or "unknown",
        "stage": job.get("stage"),
        "progress": job.get("progress") or 0,
        "message": job.get("message") or "",
        "error": job.get("error"),
        "result": job.get("result"),
        "updated_at": job.get("updated_at"),
    }


@app.get("/research/drafts/{draft_id}")
def get_research_draft(draft_id: str, user=Depends(require_user)):
    from research_drafts import load_draft

    draft = load_draft(draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found or expired")
    if draft.get("user_id") and draft["user_id"] != user["id"]:
        raise HTTPException(403, "Not your draft")
    return {
        "status": "ok",
        "draft_id": draft_id,
        "needs_rating": True,
        "name": draft.get("name"),
        "company": draft.get("company"),
        "university": draft.get("university"),
        "linkedin_url": draft.get("linkedin_url"),
        "contact": (
            {"linkedin_url": draft["linkedin_url"]}
            if draft.get("linkedin_url")
            else {}
        ),
        "summary": _public_summary(draft.get("summary") or {}),
        "conversation": draft.get("conversation") or {"status": "skipped"},
        "mutuals": draft.get("mutuals") or [],
        "in_your_network": draft.get("in_your_network"),
        "usage": draft.get("usage"),
        "sources": {
            "exa_search": {
                "linkedin_url": (
                    (draft.get("all_sources") or {}).get("exa_search") or {}
                ).get("linkedin_url")
                or draft.get("linkedin_url"),
                "status": ((draft.get("all_sources") or {}).get("exa_search") or {}).get("status"),
            }
            if (draft.get("all_sources") or {}).get("exa_search") or draft.get("linkedin_url")
            else None,
            "gemini_search": {
                "linkedin_url": (
                    ((draft.get("all_sources") or {}).get("gemini_search") or {}).get(
                        "social_profile_links"
                    )
                    or {}
                ).get("linkedin")
                or ((draft.get("all_sources") or {}).get("gemini_search") or {}).get("linkedin_url")
                or draft.get("linkedin_url"),
                "status": ((draft.get("all_sources") or {}).get("gemini_search") or {}).get("status"),
            }
            if (draft.get("all_sources") or {}).get("gemini_search") or draft.get("linkedin_url")
            else None,
        },
    }


@app.post("/research/feedback")
def research_feedback(body: ResearchFeedbackBody, user=Depends(require_user)):
    """Rate a research draft. good → commit; bad → store corrections and re-research by default."""
    from research_drafts import delete_draft, load_draft
    from research_feedback import record_feedback
    from people_lookup import public_dossier_from_record

    draft = load_draft(body.draft_id)
    profile = user.get("profile") or {}
    stale_draft = False

    # Stale draft IDs are common after claim/expiry — still allow Bad → re-research from profile.
    if not draft:
        if body.rating != "bad":
            raise HTTPException(404, "Draft not found or expired — run research again")
        name = (profile.get("name") or "").strip()
        if not name:
            raise HTTPException(404, "Draft not found or expired — run research again")
        stale_draft = True
        company = (profile.get("current_company") or "").strip() or None
        university = None
        linkedin_url = ((profile.get("contact") or {}).get("linkedin_url") or "").strip() or None
        summary: dict[str, Any] = {}
        merged: dict[str, Any] = {}
        overlap = None
        usage: dict[str, Any] = {"tier": "self"}
        all_sources: dict[str, Any] = {}
        is_self = True
        draft = {
            "name": name,
            "company": company,
            "linkedin_url": linkedin_url,
            "kind": "self_research",
            "tier": "self",
            "user_id": user["id"],
            "conversation": {"status": "skipped"},
        }
    else:
        if draft.get("user_id") and draft["user_id"] != user["id"]:
            raise HTTPException(403, "Not your draft")
        name = draft.get("name") or ""
        company = draft.get("company")
        university = draft.get("university")
        linkedin_url = draft.get("linkedin_url")
        summary = draft.get("summary") or {}
        merged = draft.get("merged") or {}
        overlap = draft.get("common_ground")
        usage = draft.get("usage") or {}
        all_sources = draft.get("all_sources") or draft.get("raw_results") or {}
        is_self = draft.get("kind") == "self_research" or draft.get("tier") == "self"

    if body.rating == "bad":
        if not (body.wrong_notes or "").strip():
            raise HTTPException(
                400,
                "Tell us what was wrong so the next research can fix it.",
            )
        record_feedback(
            user_id=user["id"],
            rating="bad",
            name=name,
            company=company,
            university=university,
            linkedin_url=linkedin_url,
            draft_id=body.draft_id,
            wrong_notes=body.wrong_notes,
            wrong_categories=body.wrong_categories,
            briefing_snapshot=_public_summary(summary) if isinstance(summary, dict) else None,
        )
        try:
            delete_draft(body.draft_id)
        except Exception:
            pass

        if is_self:
            failed_profile = dict(profile)
            failed_profile["research_status"] = "retrying" if body.auto_retry else "discarded"
            failed_profile.pop("research_draft_id", None)
            failed_profile["research_error"] = (body.wrong_notes or "Rated bad")[:500]
            user = users.replace_profile(user["id"], failed_profile)

            if body.auto_retry:
                retry = _run_self_research(
                    user,
                    SelfResearchBody(
                        company=company,
                        university=university,
                        linkedin_url=linkedin_url,
                        force_refresh=True,
                        auto_commit=False,
                    ),
                )
                if retry.get("status") != "ok":
                    return {
                        "status": "ok",
                        "rating": "bad",
                        "committed": False,
                        "retried": False,
                        "message": (
                            "Saved your notes, but re-research failed: "
                            f"{retry.get('error') or 'unknown error'}"
                        ),
                        "user": _public_user(users.get(user["id"]) or user),
                    }
                return {
                    "status": "ok",
                    "rating": "bad",
                    "committed": False,
                    "retried": True,
                    "needs_rating": True,
                    "draft_id": retry.get("draft_id"),
                    "summary": retry.get("summary"),
                    "name": retry.get("name") or name,
                    "company": retry.get("company") or company,
                    "message": "Re-researched with your corrections — review the new draft.",
                    "user": retry.get("user") or _public_user(users.get(user["id"]) or user),
                }

            return {
                "status": "ok",
                "rating": "bad",
                "committed": False,
                "retried": False,
                "message": "Draft discarded. Corrections will guide the next research.",
                "user": _public_user(user),
            }

        return {
            "status": "ok",
            "rating": "bad",
            "committed": False,
            "retried": False,
            "retry": True,
            "name": name,
            "company": company,
            "university": university,
            "linkedin_url": linkedin_url,
            "message": "Draft discarded. Re-researching with your corrections…",
        }

    if stale_draft or not merged:
        raise HTTPException(404, "Draft not found or expired — run research again")

    path = profiles.save(
        name,
        company,
        merged,
        summary,
        common_ground=overlap,
        usage=usage,
    )
    rec = profiles.load(name, company) or {}
    rec["visibility"] = "public"
    if university:
        rec["university"] = university
    if linkedin_url:
        contact = dict(rec.get("contact") or {})
        contact["linkedin_url"] = linkedin_url
        rec["contact"] = contact
    if draft.get("mutuals") is not None:
        rec["mutuals"] = draft.get("mutuals")
    if draft.get("in_your_network") is not None:
        rec["in_your_network"] = draft.get("in_your_network")
    path.write_text(__import__("json").dumps(rec, indent=2))

    profiles.record_interaction(
        name,
        company,
        {
            "type": "research_commit",
            "tier": usage.get("tier"),
            "tokens_charged": usage.get("tokens_charged"),
            "user_id": user["id"],
            "rating": "good",
        },
    )
    record_feedback(
        user_id=user["id"],
        rating="good",
        name=name,
        company=company,
        university=university,
        linkedin_url=linkedin_url,
        person_slug=path.stem,
        draft_id=body.draft_id,
    )

    if is_self:
        researched = profile_from_research(
            name=name,
            company=company,
            summary=summary,
            sources=all_sources,
            contact=profile.get("contact") or {},
            linkedin_url=linkedin_url,
        )
        researched["researched_at"] = datetime.now(timezone.utc).isoformat()
        researched["research_status"] = "ok"
        researched["self_profile_slug"] = path.stem
        researched["profile_source"] = "claimed_public"
        researched.pop("research_draft_id", None)
        merged_profile = merge_manual_overlays(researched, profile)
        user = users.replace_profile(user["id"], merged_profile)
        rec["claimed_user_id"] = user["id"]
        path.write_text(__import__("json").dumps(rec, indent=2))

    delete_draft(body.draft_id)
    dossier = public_dossier_from_record(rec)
    contact_out = dict(dossier.get("contact") or {})
    if linkedin_url:
        contact_out["linkedin_url"] = linkedin_url
    return {
        "status": "ok",
        "rating": "good",
        "committed": True,
        "name": name,
        "company": company,
        "profile_slug": path.stem,
        "summary": _public_summary(summary),
        "conversation": draft.get("conversation") or {"status": "skipped"},
        "public": dossier,
        "contact": contact_out,
        "linkedin_url": linkedin_url or contact_out.get("linkedin_url"),
        "user": _public_user(user),
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


@app.patch("/me/settings")
def update_settings(body: SettingsBody, user=Depends(require_user)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "auto_prep" in patch:
        cal = dict((user.get("settings") or {}).get("calendar") or {})
        cal["auto_prep"] = patch.pop("auto_prep")
        patch["calendar"] = cal
    user = users.update_settings(user["id"], patch)
    return _public_user(user)


@app.post("/auth/signup/otp/send")
def signup_otp_send(body: SignupOtpSendBody):
    """Pre-auth: send email OTP before account creation."""
    from otp import issue_signup_email_otp

    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Valid email required")
    if users.find_by_email(email):
        raise HTTPException(400, "Email already registered")
    try:
        return issue_signup_email_otp(email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/auth/signup/otp/verify")
def signup_otp_verify(body: SignupOtpVerifyBody):
    """Pre-auth: verify email OTP; returns email_verified_token for /auth/signup."""
    from otp import verify_signup_email_otp

    email = (body.email or "").strip().lower()
    result = verify_signup_email_otp(email, body.code)
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("error") or "OTP failed")
    return result


@app.post("/auth/otp/send")
def otp_send(body: OtpSendBody, user=Depends(require_user)):
    from otp import issue_otp

    dest = (body.destination or "").strip()
    if body.channel == "email":
        dest = dest or user.get("email") or ""
    else:
        dest = dest or ((user.get("profile") or {}).get("contact") or {}).get("phone") or ""
    if not dest:
        raise HTTPException(400, f"No {body.channel} on file — provide destination")
    return issue_otp(user["id"], body.channel, dest)


@app.post("/auth/otp/verify")
def otp_verify(body: OtpVerifyBody, user=Depends(require_user)):
    from otp import verify_otp

    result = verify_otp(user["id"], body.channel, body.code)
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("error") or "OTP failed")
    profile = dict(user.get("profile") or {})
    verified = dict(profile.get("identity_verified") or {})
    verified[body.channel] = {
        "verified": True,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    profile["identity_verified"] = verified
    users.replace_profile(user["id"], profile)
    return {"ok": True, "user": _public_user(users.get(user["id"]))}


@app.get("/public/stats")
def public_stats():
    """Landing-page stats (user count + review placeholders)."""
    count = users.count()
    return {
        "user_count": count,
        "user_count_display": f"{count:,}" if count else "0",
        "reviews": [
            {
                "id": "placeholder-1",
                "quote": "Walked into the meeting already knowing what we shared. Felt natural, not stalky.",
                "name": "Alex M.",
                "role": "Founder",
                "placeholder": True,
            },
            {
                "id": "placeholder-2",
                "quote": "The LinkedIn lock alone saved me from briefing the wrong person twice.",
                "name": "Priya S.",
                "role": "BD lead",
                "placeholder": True,
            },
            {
                "id": "placeholder-3",
                "quote": "Common ground tips that actually matched — not generic icebreakers.",
                "name": "Jordan L.",
                "role": "Investor",
                "placeholder": True,
            },
        ],
        "icp": {
            "headline": "Built for people who meet for a living",
            "body": (
                "Founders, operators, investors, and sales leaders who prep before "
                "intros — and refuse to mix up same-name strangers."
            ),
            "segments": [
                "Founders & operators",
                "Investors & advisors",
                "BD / partnerships",
                "Recruiters & talent",
            ],
        },
    }


@app.post("/verify/handles")
def verify_handles_endpoint(body: VerifyHandlesBody):
    """Pre-account handle verification for signup — call once; cache results client-side."""
    from handle_verify import verify_handles

    return verify_handles(
        name=body.name.strip(),
        company=(body.company or "").strip() or None,
        university=(body.university or "").strip() or None,
        linkedin_url=(body.linkedin_url or "").strip() or None,
        handles={k: v for k, v in (body.handles or {}).items() if v},
    )


@app.post("/identity/resolve")
def identity_resolve_endpoint(body: IdentityResolveBody, user=Depends(require_user)):
    """Score LinkedIn ↔ GitHub without auto-merging. Returns score/tier/evidence only."""
    from identity_resolve import public_resolution, resolve_linkedin_github

    if not (body.github_username or body.github_url):
        raise HTTPException(400, "github_username or github_url required")
    result = resolve_linkedin_github(
        linkedin_url=body.linkedin_url.strip(),
        github_username=(body.github_username or "").strip() or None,
        github_url=(body.github_url or "").strip() or None,
        name=(body.name or "").strip() or None,
        company=(body.company or "").strip() or None,
        location=(body.location or "").strip() or None,
        known_email=(body.known_email or "").strip() or None,
    )
    return {"status": "ok", "match": public_resolution(result), "meta": result.get("_meta")}


@app.get("/identity/queue")
def identity_queue_list(user=Depends(require_user)):
    """Human-in-the-loop queue for possible (unconfirmed) identity matches."""
    from identity_resolve import list_possible_queue

    return {"status": "ok", "queue": list_possible_queue()}


@app.post("/identity/queue/{item_id}")
def identity_queue_decide(item_id: str, body: IdentityQueueDecisionBody, user=Depends(require_user)):
    from identity_resolve import resolve_queue_item

    rec = resolve_queue_item(item_id, body.decision)
    if not rec:
        raise HTTPException(404, "Queue item not found")
    return {"status": "ok", "item": rec}


@app.post("/me/connections")
def upload_connections(body: ConnectionsUploadBody, user=Depends(require_user)):
    from connections import parse_linkedin_connections_csv

    rows = parse_linkedin_connections_csv(body.csv)
    if not rows:
        raise HTTPException(400, "No connections parsed — check LinkedIn Connections CSV format")
    users.replace_connections(user["id"], rows)
    return {"ok": True, "imported": len(rows), "filename": body.filename}


@app.get("/me/connections")
def get_connections(user=Depends(require_user)):
    conns = user.get("connections") or []
    return {
        "count": len(conns),
        "imported_at": user.get("connections_imported_at"),
        "sample": conns[:5],
    }


@app.get("/me/private/journal")
def get_private_journal(user=Depends(require_user)):
    from private_journal import list_entries

    return {"visibility": "private", "entries": list_entries(user)}


@app.post("/me/private/journal")
def post_private_journal(body: JournalEntryBody, user=Depends(require_user)):
    from private_journal import add_entry, list_entries

    try:
        entry = add_entry(
            users,
            user["id"],
            body=body.body,
            entry_type=body.entry_type,
            tags=body.tags,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "entry": entry, "entries": list_entries(users.get(user["id"]))}


@app.delete("/me/private/journal/{entry_id}")
def delete_private_journal(entry_id: str, user=Depends(require_user)):
    from private_journal import delete_entry, list_entries

    delete_entry(users, user["id"], entry_id)
    return {"ok": True, "entries": list_entries(users.get(user["id"]))}


@app.post("/me/pending-facts")
def add_pending_fact(body: PendingFactBody, user=Depends(require_user)):
    status = "trusted_personal" if body.trusted_personal else "pending"
    users.add_pending_fact(
        user["id"],
        {
            "claim": body.claim.strip(),
            "person_name": body.person_name,
            "person_company": body.person_company,
            "status": status,
        },
    )
    return {"ok": True, "pending_facts": (users.get(user["id"]) or {}).get("pending_facts") or []}


@app.patch("/me/pending-facts/{fact_id}")
def patch_pending_fact(fact_id: str, body: PendingFactUpdateBody, user=Depends(require_user)):
    users.update_pending_fact(user["id"], fact_id, body.status, body.evidence)
    return {"ok": True, "pending_facts": (users.get(user["id"]) or {}).get("pending_facts") or []}


@app.get("/calendar/oauth-url")
def calendar_oauth_url(redirect_uri: str, user=Depends(require_user)):
    from calendar_prep import oauth_authorize_url

    return oauth_authorize_url(redirect_uri, state=user["id"])


@app.post("/calendar/oauth")
def calendar_oauth(body: CalendarOAuthBody, user=Depends(require_user)):
    from calendar_prep import exchange_code, store_calendar_link

    tokens = exchange_code(body.code, body.redirect_uri)
    if tokens.get("status") != "ok":
        raise HTTPException(400, tokens.get("error") or tokens.get("reason") or "OAuth failed")
    store_calendar_link(users, user["id"], tokens)
    return {"ok": True}


@app.post("/calendar/sync-prep")
def calendar_sync_prep(user=Depends(require_user)):
    from calendar_prep import enqueue_from_calendar

    return enqueue_from_calendar(users, user["id"])


@app.get("/calendar/prep-queue")
def calendar_prep_queue(user=Depends(require_user)):
    queue = ((user.get("settings") or {}).get("meeting_prep_queue")) or []
    return {"queue": queue}


# ─── research ───────────────────────────────────────────────────────────────


@app.post("/candidates")
def candidates(body: CandidatesBody, user=Depends(require_user)):
    return _candidates_impl(body)


@app.post("/public/candidates")
def public_candidates(body: CandidatesBody):
    """Unauthenticated name disambiguation for signup (name-first flow)."""
    return _candidates_impl(body)


@app.post("/public/research/start")
def public_research_start(body: PublicResearchBody):
    """Pre-auth research for signup. Poll GET /public/research/jobs/{job_id}."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    company = (body.company or "").strip() or None
    university = (body.university or "").strip() or None
    place = (body.place or "").strip() or None
    linkedin_url = (body.linkedin_url or "").strip() or None
    if not (company or university or linkedin_url):
        raise HTTPException(
            400,
            "Pick a match (or add company, university, or LinkedIn) before researching.",
        )

    job_id = uuid.uuid4().hex
    _job_update(
        job_id,
        status="running",
        stage="queued",
        progress=0.01,
        message="Queued…",
        kind="public_signup",
        result=None,
        error=None,
    )

    def worker() -> None:
        def on_progress(stage: str, progress: float, message: str) -> None:
            _job_update(
                job_id,
                status="running",
                stage=stage,
                progress=max(0.0, min(0.99, float(progress))),
                message=message,
            )

        try:
            on_progress("queued", 0.02, "Starting research…")
            briefing = _fetch_person_briefing(
                name=name,
                company=company,
                university=university,
                place=place,
                linkedin_url=linkedin_url,
                fetch_social=True,
                force_refresh=body.force_refresh,
                persist=False,
                user_id=None,
                on_progress=on_progress,
            )
            if briefing.get("status") != "ok":
                err = briefing.get("error") or "Research failed"
                _job_update(
                    job_id,
                    status="error",
                    stage="error",
                    progress=1.0,
                    message=err,
                    error=err,
                    result=None,
                )
                return
            result = {
                "status": "ok",
                "draft_id": briefing.get("draft_id"),
                "needs_rating": True,
                "name": name,
                "company": company,
                "university": university,
                "place": place,
                "linkedin_url": linkedin_url,
                "summary": _public_summary(briefing.get("summary") or {}),
            }
            _job_update(
                job_id,
                status="done",
                stage="done",
                progress=1.0,
                message="Research ready for your review",
                result=result,
                error=None,
            )
        except Exception as exc:
            _job_update(
                job_id,
                status="error",
                stage="error",
                progress=1.0,
                message=str(exc)[:300],
                error=str(exc)[:300],
                result=None,
            )

    threading.Thread(target=worker, daemon=True).start()
    return {"status": "ok", "job_id": job_id}


@app.get("/public/research/jobs/{job_id}")
def public_research_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("kind") != "public_signup":
        raise HTTPException(403, "Not a public research job")
    return {
        "status": job.get("status") or "unknown",
        "stage": job.get("stage"),
        "progress": job.get("progress") or 0,
        "message": job.get("message") or "",
        "error": job.get("error"),
        "result": job.get("result"),
        "updated_at": job.get("updated_at"),
    }


@app.post("/public/research/feedback")
def public_research_feedback(body: PublicResearchFeedbackBody):
    """Pre-auth Bad rating: store corrections and optionally start a corrected research job."""
    from research_drafts import delete_draft, load_draft
    from research_feedback import record_feedback

    draft = load_draft(body.draft_id)
    if not draft:
        raise HTTPException(404, "Draft not found or expired — research again")
    if draft.get("user_id"):
        raise HTTPException(403, "Use authenticated /research/feedback for this draft")
    if body.rating != "bad":
        raise HTTPException(400, "Public feedback only supports Bad — create an account to save Good")
    if not (body.wrong_notes or "").strip():
        raise HTTPException(400, "Tell us what was wrong so the next research can fix it.")

    name = (draft.get("name") or "").strip()
    company = draft.get("company")
    university = draft.get("university")
    place = draft.get("place")
    linkedin_url = draft.get("linkedin_url")
    record_feedback(
        user_id=None,
        rating="bad",
        name=name,
        company=company,
        university=university,
        linkedin_url=linkedin_url,
        draft_id=body.draft_id,
        wrong_notes=body.wrong_notes,
        wrong_categories=body.wrong_categories or ["signup_self_research"],
        briefing_snapshot=_public_summary(draft.get("summary") or {}),
    )
    try:
        delete_draft(body.draft_id)
    except Exception:
        pass

    if not body.auto_retry:
        return {
            "status": "ok",
            "rating": "bad",
            "retried": False,
            "message": "Notes saved. Tap research again when ready.",
        }

    # Kick off corrected research as a public job
    started = public_research_start(
        PublicResearchBody(
            name=name,
            company=company,
            university=university,
            place=place,
            linkedin_url=linkedin_url,
            force_refresh=body.force_refresh,
        )
    )
    return {
        "status": "ok",
        "rating": "bad",
        "retried": True,
        "job_id": started.get("job_id"),
        "message": "Re-researching with your corrections…",
    }


def _candidates_impl(body: CandidatesBody):
    from connectors import gemini_search

    name = body.name.strip()
    print("=" * 60, flush=True)
    print(f"[/public/candidates] REQUEST name={name!r}", flush=True)
    print(
        f"  filters company={body.company!r} university={body.university!r} "
        f"linkedin={body.linkedin_url!r}",
        flush=True,
    )
    if not name:
        print("[/public/candidates] REJECT empty name", flush=True)
        raise HTTPException(400, "Name required")

    result = gemini_search.find_candidates(
        name,
        company=(body.company or "").strip() or None,
        university=(body.university or "").strip() or None,
        linkedin_url=(body.linkedin_url or "").strip() or None,
    )
    status = result.get("status")
    err = result.get("error") or result.get("reason")
    print(f"[/public/candidates] gemini status={status!r} error={err!r}", flush=True)

    if status == "skipped":
        print(f"[/public/candidates] SKIPPED: {err}", flush=True)
        return {"candidates": [], "status": status, "error": err or "Gemini unavailable"}
    if status == "error":
        print(f"[/public/candidates] ERROR: {err}", flush=True)
        return {"candidates": [], "status": status, "error": err or "Candidate search failed"}
    if status != "ok":
        print(f"[/public/candidates] not_found / empty — returning []", flush=True)
        return {"candidates": [], "status": status or "not_found", "error": err}

    def _apply_filters(rows: list) -> list:
        from connectors.exa_search import _candidate_matches_org, _org_aliases

        out_rows = list(rows or [])
        company = (body.company or "").strip() or None
        university = (body.university or "").strip() or None
        linkedin = (body.linkedin_url or "").strip().lower()
        if company or university:
            filtered = [
                c
                for c in out_rows
                if _candidate_matches_org(c, company=company, university=university)
            ]
            # Prefer org matches; only fall back to full list if discovery found none
            if filtered:
                out_rows = filtered
            else:
                # Also try loose alias substring on context (USC vs full name)
                aliases = _org_aliases(university) + _org_aliases(company)
                loose = []
                for c in out_rows:
                    blob = " ".join(
                        [
                            str(c.get("company") or ""),
                            str(c.get("context") or ""),
                            str(c.get("role") or ""),
                            str(c.get("location") or ""),
                        ]
                    ).lower()
                    if any(a.lower() in blob for a in aliases if a):
                        loose.append(c)
                if loose:
                    out_rows = loose
        if linkedin:
            filtered = [
                c
                for c in out_rows
                if linkedin in (c.get("linkedin_url") or "").lower()
                or linkedin.replace("https://", "") in (c.get("linkedin_url") or "").lower()
            ]
            if filtered:
                out_rows = filtered
        return out_rows

    exact = _apply_filters(list(result.get("exact") or []))
    probable = _apply_filters(list(result.get("probable") or []))
    match_mode = result.get("match_mode") or ("exact" if exact else "probable_only" if probable else "none")

    # Filters may empty the preferred bucket — fall back to the other.
    if match_mode == "exact" and not exact and probable:
        match_mode = "probable_only"
    if match_mode == "probable_only" and not probable and exact:
        match_mode = "exact"

    if match_mode == "exact":
        cands = exact
        probable = []  # UI shows exact only
    elif match_mode == "probable_only":
        cands = probable
        exact = []
    else:
        # Passthrough / empty — keep find_candidates display list
        cands = _apply_filters(list(result.get("candidates") or []))

    from name_match import exact_match_message

    message = result.get("message") or exact_match_message(match_mode)

    print(
        f"[/public/candidates] RESPONSE ok match_mode={match_mode} "
        f"exact={len(exact)} probable={len(probable)} display={len(cands)}",
        flush=True,
    )
    for i, c in enumerate(cands):
        print(f"  → [{i}] {c.get('name')} @ {c.get('company')} | {c.get('role')}", flush=True)
    print("=" * 60, flush=True)
    out = {
        "candidates": cands,
        "exact": exact,
        "probable": probable,
        "match_mode": match_mode,
        "status": "ok",
    }
    if message:
        out["message"] = message
    if result.get("warning"):
        out["warning"] = result["warning"]
    if result.get("discovery"):
        out["discovery"] = result["discovery"]
    return out


@app.post("/research")
def research(body: ResearchBody, user=Depends(require_user)):
    # Filters optional if a cached dossier can match; otherwise need disambiguator
    has_filter = bool(
        (body.company or "").strip()
        or (body.university or "").strip()
        or (body.linkedin_url or "").strip()
    )
    from people_lookup import find_cached_person

    cached = find_cached_person(
        profiles,
        name=body.name.strip(),
        company=(body.company or "").strip() or None,
        university=(body.university or "").strip() or None,
        linkedin_url=(body.linkedin_url or "").strip() or None,
    )
    if not has_filter and not cached and not body.force_refresh:
        raise HTTPException(400, "Company, university, or LinkedIn URL required (or pick a cached match)")

    cost = TOKEN_COST_DETAILED if body.tier == "detailed" else TOKEN_COST_BASIC
    if user.get("tokens", 0) < 1:
        raise HTTPException(402, "Not enough tokens")

    result = _run_research(body, user)
    if result.get("status") == "ok":
        charge = int((result.get("usage") or {}).get("tokens_charged") or cost)
        charge = max(0, min(charge, cost))
        if charge:
            if user.get("tokens", 0) < charge:
                raise HTTPException(402, f"Not enough tokens (need {charge})")
            users.charge_tokens(user["id"], charge, reason=f"research:{body.tier}:{body.name}")
        users.append_interaction(
            user["id"],
            {
                "type": "research",
                "person_name": body.name,
                "company": body.company,
                "tier": body.tier,
                "tokens_charged": charge,
                "from_cache": result.get("from_cache"),
                "profile_slug": result.get("profile_slug"),
            },
        )
        user = users.get(user["id"])
        result["tokens_remaining"] = user.get("tokens")
    result.pop("_engine", None)
    return result


@app.post("/people/{name}/refresh")
def refresh_person(
    name: str,
    company: Optional[str] = None,
    fetch_social: bool = False,
    user=Depends(require_user),
):
    """Incremental refresh — only re-fetches stale sources; returns what's new."""
    ttl_hours = float(os.environ.get("CACHE_TTL_HOURS", "24"))
    fresh, stale = profiles.freshness(name, company, ttl_hours)
    existing = profiles.load(name, company)
    if not stale:
        wn = (existing or {}).get("whats_new") or {"changes": []}
        return {
            "status": "ok",
            "refreshed": False,
            "reason": "all_sources_fresh",
            "whats_new": wn,
            "fresh_sources": sorted(fresh),
        }
    if user.get("tokens", 0) < 1:
        raise HTTPException(402, "Need 1 token to refresh stale sources")
    result = _run_research(
        ResearchBody(
            name=name,
            company=company,
            tier="basic",
            fetch_social=fetch_social,
            force_refresh=False,
        ),
        user,
    )
    if result.get("status") == "ok":
        users.charge_tokens(user["id"], 1, reason=f"refresh:{name}")
    result.pop("_engine", None)
    record = profiles.load(name, company) or {}
    result["whats_new"] = record.get("whats_new") or {"changes": []}
    result["refreshed"] = True
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
    from people_lookup import public_dossier_from_record

    record = profiles.load(name, company)
    if not record:
        raise HTTPException(404, "Person not found in your research cache")
    sources = record.get("latest_sources") or {}
    engine = record.get("latest_common_ground")
    summary = record.get("latest_summary") or {}
    conversation = public_conversation(engine) if engine else public_conversation(
        summary.get("conversation") or summary.get("common_ground")
    )
    # Mutuals from user's LinkedIn CSV (evidence only)
    from connections import find_person_in_connections, match_mutuals

    connections = user.get("connections") or []
    mutuals = match_mutuals(name, company, connections) if connections else []
    in_network = find_person_in_connections(name, connections, company=company) if connections else None
    pending = [
        f
        for f in (user.get("pending_facts") or [])
        if (f.get("person_name") or "").lower() == name.lower()
    ]
    dossier = public_dossier_from_record(record)
    return {
        "visibility": "public",
        "name": record.get("name"),
        "company": record.get("company"),
        "university": record.get("university"),
        "contact": dossier.get("contact") or {},
        "summary": _public_summary(summary),
        "conversation": conversation,
        "public": dossier,
        "usage": {
            "tier": (record.get("latest_usage") or {}).get("tier"),
            "tokens_charged": (record.get("latest_usage") or {}).get("tokens_charged"),
            "from_cache": (record.get("latest_usage") or {}).get("from_cache"),
        },
        "interactions": record.get("interactions") or [],
        "updated_at": record.get("updated_at"),
        "source_status": record.get("latest_source_status") or {},
        "whats_new": record.get("whats_new"),
        "mutuals": mutuals,
        "in_your_network": in_network,
        "pending_facts": pending,
        "sources": {
            "apollo": sources.get("apollo"),
            "public_web": sources.get("public_web"),
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


@app.post("/people/{name}/chat")
def person_chat(name: str, body: PersonChatBody, user=Depends(require_user)):
    """Ask questions about a researched person using dossier + conversation context only."""
    from person_chat import ask_about_person
    from people_lookup import public_dossier_from_record

    company = body.company
    you = (user.get("profile") or {}) if isinstance(user.get("profile"), dict) else {}

    if body.draft_id:
        from research_drafts import load_draft

        draft = load_draft(body.draft_id)
        if not draft:
            raise HTTPException(404, "Draft not found")
        summary = draft.get("summary") or {}
        conversation = draft.get("conversation") or {}
        contact = draft.get("contact") or {}
        sources = draft.get("sources") or {}
        result = ask_about_person(
            question=body.question,
            name=draft.get("name") or name,
            company=draft.get("company") or company,
            summary=_public_summary(summary) if summary else {},
            conversation=conversation if isinstance(conversation, dict) else {},
            contact=contact,
            public_profile={},
            sources={
                "instagram_public": sources.get("instagram_public"),
                "facebook_public": sources.get("facebook_public"),
                "twitter_public": sources.get("twitter_public"),
                "linkedin_public": sources.get("linkedin_public"),
            },
            you_profile=you,
            history=body.history or [],
        )
    else:
        record = profiles.load(name, company)
        if not record:
            raise HTTPException(404, "Person not found in your research cache")

        sources = record.get("latest_sources") or {}
        engine = record.get("latest_common_ground")
        summary = record.get("latest_summary") or {}
        conversation = public_conversation(engine) if engine else public_conversation(
            summary.get("conversation") or summary.get("common_ground")
        )
        dossier = public_dossier_from_record(record)
        result = ask_about_person(
            question=body.question,
            name=record.get("name") or name,
            company=record.get("company") or company,
            summary=_public_summary(summary) if summary else {},
            conversation=conversation if isinstance(conversation, dict) else {},
            contact=dossier.get("contact") or {},
            public_profile=dossier,
            sources={
                "instagram_public": sources.get("instagram_public"),
                "facebook_public": sources.get("facebook_public"),
                "twitter_public": sources.get("twitter_public"),
                "linkedin_public": sources.get("linkedin_public"),
            },
            you_profile=you,
            history=body.history or [],
        )

    if result.get("status") == "skipped":
        raise HTTPException(503, result.get("error") or "Chat unavailable")
    if result.get("status") != "ok":
        raise HTTPException(400, result.get("error") or "Chat failed")
    return result


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
    from calendar_prep import calendar_configured
    from db import supabase_enabled

    return {
        "ok": True,
        "service": "connect-deeply",
        "apollo_configured": bool((os.environ.get("APOLLO_API_KEY") or "").strip()),
        "aleads_configured": bool(
            (os.environ.get("ALEADS_API_KEY") or os.environ.get("A_LEADS_API_KEY") or "").strip()
        ),
        "enrichlayer_configured": bool(
            (os.environ.get("ENRICHLAYER_API_KEY") or os.environ.get("ENRICH_LAYER_API_KEY") or "").strip()
        ),
        "nimble_configured": bool((os.environ.get("NIMBLE_API_KEY") or "").strip()),
        "supabase_configured": supabase_enabled(),
        "calendar_configured": calendar_configured(),
    }


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
    persist: bool = True,
    user_id: Optional[str] = None,
    on_progress: Optional[Any] = None,
) -> dict[str, Any]:
    """Research + summarize a person (no conversation / no token charge).

    When persist=False, stages a draft for good/bad rating instead of writing people DB.
    """
    def _prog(stage: str, progress: float, message: str) -> None:
        if not on_progress:
            return
        try:
            on_progress(stage, progress, message)
        except Exception:
            pass

    ttl_hours = float(os.environ.get("CACHE_TTL_HOURS", "24"))
    if force_refresh:
        fresh: set = set()
    else:
        fresh, _ = profiles.freshness(name, company, ttl_hours)

    skip = set(fresh)
    if not fetch_social:
        skip |= set(SOCIAL_SOURCES)

    existing = profiles.load(name, company)
    try:
        from research_feedback import merged_search_constraints

        search_constraints = merged_search_constraints(
            name=name, company=company, linkedin_url=linkedin_url
        ) or None
    except Exception:
        search_constraints = None
    if search_constraints:
        print(f"  [research] applying feedback search constraints: {list(search_constraints.keys())}", flush=True)

    raw_results = orchestrator.run(
        PersonQuery(
            name=name,
            company=company,
            university=university,
            place=place,
            linkedin_url=linkedin_url,
            github_username=github_username,
            search_constraints=search_constraints,
        ),
        skip=frozenset(skip),
        on_progress=on_progress,
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

    # Chosen LinkedIn is source of truth — strip Peerlist/GitHub/posts from other same-name people
    if linkedin_url:
        from identity_filter import filter_sources_against_linkedin

        raw_results = filter_sources_against_linkedin(
            linkedin_url=linkedin_url,
            sources=raw_results,
            company=company,
            university=university,
            name=name,
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
        _prog("synthesize", 0.92, "Using cached briefing")
    else:
        from research_feedback import corrections_prompt_block, mark_applied, prior_bad_corrections

        _prog("synthesize", 0.88, "Building your public briefing…")
        cached_sources = (existing or {}).get("latest_sources", {})
        llm_view = copy.deepcopy(merged)
        llm_view["sources"] = {**cached_sources, **raw_results}
        prior = prior_bad_corrections(name=name, company=company, linkedin_url=linkedin_url)
        if prior:
            llm_view["prior_user_corrections"] = prior
            llm_view["prior_user_corrections_text"] = corrections_prompt_block(prior)
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
        elif prior:
            mark_applied([p["id"] for p in prior if p.get("id")])
        _prog("synthesize", 0.95, "Briefing ready")

    cached_sources = (existing or {}).get("latest_sources", {})
    all_sources = {**cached_sources, **raw_results}

    if persist:
        path = profiles.save(name, company, merged, summary, common_ground=None, usage={"tier": "self"})
        return {
            "status": "ok",
            "name": name,
            "company": company,
            "summary": summary,
            "sources": all_sources,
            "profile_path": str(path),
            "profile_slug": path.stem,
            "needs_rating": False,
            "draft_id": None,
        }

    from research_drafts import save_draft

    draft_id = save_draft(
        {
            "name": name,
            "company": company,
            "university": university,
            "linkedin_url": linkedin_url,
            "place": place,
            "merged": merged,
            "summary": summary,
            "common_ground": None,
            "usage": {"tier": "self", "tokens_charged": 0},
            "all_sources": all_sources,
            "raw_results": raw_results,
            "conversation": {"status": "skipped"},
            "mutuals": [],
            "in_your_network": None,
            "user_id": user_id,
            "tier": "self",
            "kind": "self_research",
        }
    )
    return {
        "status": "ok",
        "name": name,
        "company": company,
        "summary": summary,
        "sources": all_sources,
        "profile_path": None,
        "profile_slug": None,
        "needs_rating": True,
        "draft_id": draft_id,
    }


def _run_research(body: ResearchBody, user: dict) -> dict:
    name = body.name.strip()
    company = (body.company or "").strip() or None
    university = (body.university or "").strip() or None
    linkedin_url = (body.linkedin_url or "").strip() or None
    ttl_hours = float(os.environ.get("CACHE_TTL_HOURS", "24"))

    from people_lookup import find_cached_person, public_dossier_from_record
    from private_journal import find_user_claiming_person, overlap_hints_from_private

    existing = find_cached_person(
        profiles,
        name=name,
        company=company,
        university=university,
        linkedin_url=linkedin_url,
    ) or profiles.load(name, company)

    from research_feedback import (
        corrections_prompt_block,
        has_blocking_bad_feedback,
        mark_applied,
        prior_bad_corrections,
    )

    blocking_bad = has_blocking_bad_feedback(
        name=name, company=company, linkedin_url=linkedin_url
    )
    if blocking_bad:
        print("  [feedback] prior BAD rating — skip cache reuse, re-research with corrections", flush=True)

    reuse_full = (
        not body.force_refresh
        and not blocking_bad
        and existing
        and existing.get("latest_summary")
        and (existing.get("latest_summary") or {}).get("status") == "ok"
    )

    raw_results: dict = {}
    from_cache = False
    if reuse_full:
        # Reuse DB dossier — no connector / Gemini research spend
        print(f"  [cache] reusing public dossier for {name} @ {company or university or linkedin_url or '?'}")
        summary = existing["latest_summary"]
        all_sources = existing.get("latest_sources") or {}
        from_cache = True
        # Minimal merged shell for save path
        merged = {
            "fetched_at": existing.get("updated_at")
            or __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "sources": {},
            "source_status": existing.get("latest_source_status") or {},
        }
    else:
        if body.force_refresh:
            fresh: set = set()
        else:
            fresh, _ = profiles.freshness(name, company, ttl_hours)

        skip = set(fresh)
        if not body.fetch_social:
            skip |= set(SOCIAL_SOURCES)

        try:
            from research_feedback import merged_search_constraints

            search_constraints = merged_search_constraints(
                name=name, company=company, linkedin_url=linkedin_url
            ) or None
        except Exception:
            search_constraints = None

        raw_results = orchestrator.run(
            PersonQuery(
                name=name,
                company=company,
                university=university,
                place=body.place,
                linkedin_url=linkedin_url,
                search_constraints=search_constraints,
            ),
            skip=frozenset(skip),
        )

        if linkedin_url:
            from identity_filter import filter_sources_against_linkedin

            raw_results = filter_sources_against_linkedin(
                linkedin_url=linkedin_url,
                sources=raw_results,
                company=company,
                university=university,
                name=name,
            )

        merged = merge_profile(
            query={
                "name": name,
                "company": company,
                "university": university,
                "place": body.place,
                "linkedin_url": linkedin_url,
            },
            raw_results=raw_results,
        )

        has_new_data = any(r.get("status") == "ok" for r in raw_results.values())
        if not has_new_data and existing and existing.get("latest_summary"):
            summary = existing["latest_summary"]
            from_cache = True
        else:
            cached_sources = (existing or {}).get("latest_sources", {})
            llm_view = copy.deepcopy(merged)
            llm_view["sources"] = {**cached_sources, **raw_results}
            prior = prior_bad_corrections(
                name=name, company=company, linkedin_url=linkedin_url
            )
            if prior:
                llm_view["prior_user_corrections"] = prior
                llm_view["prior_user_corrections_text"] = corrections_prompt_block(prior)
                print(f"  [feedback] injecting {len(prior)} prior bad correction(s)", flush=True)
            summary = summarize_profile(llm_view)
            if summary.get("status") != "ok":
                cached = (existing or {}).get("latest_summary")
                if isinstance(cached, dict) and cached.get("status") == "ok":
                    summary = cached
                    from_cache = True
                else:
                    return {
                        "status": "error",
                        "error": summary.get("error") or "Summarization failed",
                        "source_status": {k: v.get("status") for k, v in raw_results.items()},
                    }
            elif prior:
                mark_applied([p["id"] for p in prior if p.get("id")])

        cached_sources = (existing or {}).get("latest_sources", {})
        all_sources = {**cached_sources, **raw_results}

    # Private overlap fuel if THEM is a claimed Connect Deeply user
    claimed = find_user_claiming_person(
        users,
        name=name,
        company=company,
        slug=(existing or {}).get("slug") if existing else None,
    )
    # Also try slug from path after save
    them_hints = overlap_hints_from_private(claimed) if claimed else {}

    overlap = None
    usage = {
        "tier": "basic",
        "tokens_charged": 0 if from_cache and body.tier == "basic" else TOKEN_COST_BASIC,
        "from_cache": from_cache,
    }
    if body.tier == "detailed" and summary.get("status") == "ok":
        you_profile = user.get("profile") or {}
        overlap = analyze_common_ground(
            summary,
            them_name=name,
            user_profile=you_profile,
            them_sources=all_sources,
            them_hints=them_hints or None,
        )
        if overlap.get("status") == "ok":
            summary = apply_overlap_to_summary(summary, overlap)
            # Cache hit: still charge detailed overlap (cheaper than full research)
            usage = {
                "tier": "detailed",
                "tokens_charged": TOKEN_COST_DETAILED if not from_cache else max(1, TOKEN_COST_DETAILED - 1),
                "from_cache": from_cache,
            }
            gaps = overlap.get("your_profile_gaps") or []
            if gaps:
                users.update_profile(
                    user["id"],
                    {"profile_refinement": {"known_gaps": gaps, "last_from": name}},
                )

    if from_cache and not raw_results:
        # Already published dossier — no rating gate
        path = profiles.path_for(name, company)
        if existing:
            existing["latest_common_ground"] = overlap
            existing["latest_usage"] = usage
            existing["visibility"] = "public"
            if university and not existing.get("university"):
                existing["university"] = university
            path.write_text(__import__("json").dumps(existing, indent=2))
        else:
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
                "from_cache": from_cache,
                "user_id": user["id"],
            },
        )
        conversation = public_conversation(overlap) if overlap else {"status": "skipped"}
        from connections import find_person_in_connections, match_mutuals

        connections = user.get("connections") or []
        mutuals = match_mutuals(name, company, connections) if connections else []
        in_network = find_person_in_connections(name, connections, company=company) if connections else None
        record = profiles.load(name, company) or {}
        if mutuals or in_network:
            record["mutuals"] = mutuals
            record["in_your_network"] = in_network
            path.write_text(__import__("json").dumps(record, indent=2))
        dossier = public_dossier_from_record(record)
        return {
            "status": "ok",
            "name": name,
            "company": company,
            "university": university,
            "profile_path": str(path),
            "profile_slug": path.stem,
            "from_cache": True,
            "needs_rating": False,
            "draft_id": None,
            "summary": _public_summary(summary),
            "conversation": conversation,
            "_engine": overlap,
            "contact": dossier.get("contact") or {},
            "public": dossier,
            "mutuals": mutuals,
            "in_your_network": in_network,
            "whats_new": record.get("whats_new"),
            "usage": usage,
            "source_status": {
                **({"_cache": "hit"} if from_cache else {}),
                **{s: "skipped" for s in SOCIAL_SOURCES if not body.fetch_social},
            },
        }

    # Fresh research: stage as draft (unless auto_commit)
    from research_drafts import save_draft

    conversation = public_conversation(overlap) if overlap else {"status": "skipped"}
    from connections import find_person_in_connections, match_mutuals

    connections = user.get("connections") or []
    mutuals = match_mutuals(name, company, connections) if connections else []
    in_network = find_person_in_connections(name, connections, company=company) if connections else None

    draft_payload = {
        "name": name,
        "company": company,
        "university": university,
        "linkedin_url": linkedin_url,
        "place": body.place,
        "merged": merged,
        "summary": summary,
        "common_ground": overlap,
        "usage": usage,
        "all_sources": all_sources,
        "raw_results": raw_results,
        "conversation": conversation,
        "mutuals": mutuals,
        "in_your_network": in_network,
        "user_id": user["id"],
        "tier": body.tier,
    }

    if body.auto_commit:
        path = profiles.save(
            name, company, merged, summary, common_ground=overlap, usage=usage
        )
        rec = profiles.load(name, company) or {}
        rec["visibility"] = "public"
        if university:
            rec["university"] = university
        if linkedin_url:
            contact = dict(rec.get("contact") or {})
            contact["linkedin_url"] = linkedin_url
            rec["contact"] = contact
        if mutuals or in_network:
            rec["mutuals"] = mutuals
            rec["in_your_network"] = in_network
        path.write_text(__import__("json").dumps(rec, indent=2))
        profiles.record_interaction(
            name,
            company,
            {
                "type": "research",
                "tier": usage.get("tier"),
                "tokens_charged": usage.get("tokens_charged"),
                "from_cache": from_cache,
                "user_id": user["id"],
                "auto_commit": True,
            },
        )
        dossier = public_dossier_from_record(rec)
        return {
            "status": "ok",
            "name": name,
            "company": company,
            "university": university,
            "profile_path": str(path),
            "profile_slug": path.stem,
            "from_cache": from_cache,
            "needs_rating": False,
            "draft_id": None,
            "summary": _public_summary(summary),
            "conversation": conversation,
            "_engine": overlap,
            "contact": dossier.get("contact") or {},
            "public": dossier,
            "mutuals": mutuals,
            "in_your_network": in_network,
            "whats_new": rec.get("whats_new"),
            "usage": usage,
            "source_status": {
                **{k: v.get("status") for k, v in raw_results.items()},
                **{s: "skipped" for s in SOCIAL_SOURCES if not body.fetch_social},
            },
        }

    draft_id = save_draft(draft_payload)
    # Charge tokens for the work even before rating (research compute was done)
    # Interaction recorded only on good commit.

    return {
        "status": "ok",
        "name": name,
        "company": company,
        "university": university,
        "profile_path": None,
        "profile_slug": None,
        "from_cache": from_cache,
        "needs_rating": True,
        "draft_id": draft_id,
        "summary": _public_summary(summary),
        "conversation": conversation,
        "_engine": overlap,
        "contact": {"linkedin_url": linkedin_url} if linkedin_url else {},
        "public": None,
        "mutuals": mutuals,
        "in_your_network": in_network,
        "whats_new": None,
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
        "settings": user.get("settings") or {},
        "connections_count": len(user.get("connections") or []),
        "pending_facts": user.get("pending_facts") or [],
        "handle_verification": profile.get("handle_verification") or {},
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
