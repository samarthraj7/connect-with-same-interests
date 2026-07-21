"""Grounded Q&A over a researched person dossier + conversation ideas."""

from __future__ import annotations

import json
import os
from typing import Any, List, Optional

from google import genai
from google.genai import types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"


def ask_about_person(
    *,
    question: str,
    name: str,
    company: Optional[str] = None,
    summary: Optional[dict] = None,
    conversation: Optional[dict] = None,
    contact: Optional[dict] = None,
    public_profile: Optional[dict] = None,
    sources: Optional[dict] = None,
    you_profile: Optional[dict] = None,
    history: Optional[List[dict]] = None,
) -> dict[str, Any]:
    """Answer from research context only — no inventing facts."""
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return {"status": "skipped", "error": "GEMINI_API_KEY not set"}

    q = (question or "").strip()
    if not q:
        return {"status": "error", "error": "question required"}
    if len(q) > 2000:
        q = q[:2000]

    context = _build_context(
        name=name,
        company=company,
        summary=summary or {},
        conversation=conversation or {},
        contact=contact or {},
        public_profile=public_profile or {},
        sources=sources or {},
        you_profile=you_profile,
    )

    hist_lines = []
    for turn in (history or [])[-8:]:
        if not isinstance(turn, dict):
            continue
        role = (turn.get("role") or "").strip().lower()
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            hist_lines.append(f"{role.upper()}: {content[:800]}")

    hist_block = ""
    if hist_lines:
        hist_block = "PRIOR TURNS:\n" + "\n".join(hist_lines) + "\n"

    context_json = json.dumps(context, ensure_ascii=False, default=str)[:28000]
    prompt = (
        "You are Connect Deeply's meeting coach chatbot.\n"
        f"Answer ONLY using the RESEARCH CONTEXT below about {name}.\n"
        "If the answer is not in the context, say you don't have that in the research yet — do not invent.\n"
        "Prefer concrete, respectful answers useful before a meeting.\n"
        "When discussing overlap / what to talk about, use conversation ideas from context "
        "(never invent private facts about the user that aren't listed).\n"
        "Keep answers concise (2–6 short paragraphs or bullets). Cite a source URL inline when the context includes one.\n\n"
        f"RESEARCH CONTEXT (JSON):\n{context_json}\n\n"
        f"{hist_block}"
        f"USER QUESTION: {q}\n"
    )
    try:
        client = genai.Client(api_key=api_key)
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=1024,
            ),
        )
        answer = (response.text or "").strip()
        if not answer:
            return {"status": "error", "error": "empty model response"}
        return {
            "status": "ok",
            "answer": answer,
            "name": name,
            "company": company,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:400]}


def _build_context(
    *,
    name: str,
    company: Optional[str],
    summary: dict,
    conversation: dict,
    contact: dict,
    public_profile: dict,
    sources: dict,
    you_profile: Optional[dict],
) -> dict:
    ig = sources.get("instagram_public") if isinstance(sources.get("instagram_public"), dict) else {}
    fb = sources.get("facebook_public") if isinstance(sources.get("facebook_public"), dict) else {}
    tw = sources.get("twitter_public") if isinstance(sources.get("twitter_public"), dict) else {}
    li = sources.get("linkedin_public") if isinstance(sources.get("linkedin_public"), dict) else {}

    social = {}
    for key, block in (("instagram", ig), ("facebook", fb), ("twitter", tw)):
        if not block or block.get("status") in (None, "skipped"):
            continue
        conf = block.get("match_confidence")
        if block.get("status") == "ambiguous" or conf == "low":
            social[key] = {
                "status": block.get("status"),
                "match_confidence": conf,
                "note": "Unverified / ambiguous — do not treat posts as confirmed.",
                "handle": block.get("handle"),
                "profile_url": block.get("profile_url"),
                "face_match": block.get("face_match"),
            }
            continue
        profile = block.get("profile") or {}
        posts = block.get("recent_posts") or []
        social[key] = {
            "status": block.get("status"),
            "match_confidence": conf,
            "handle": block.get("handle"),
            "profile_url": block.get("profile_url"),
            "bio": profile.get("biography") or profile.get("bio"),
            "face_match": block.get("face_match"),
            "recent_captions": [
                (p.get("caption") or p.get("snippet") or "")[:200]
                for p in posts[:6]
                if isinstance(p, dict)
            ],
        }

    you_slim = None
    if isinstance(you_profile, dict):
        you_slim = {
            k: you_profile.get(k)
            for k in (
                "name",
                "headline",
                "current_role",
                "current_company",
                "location",
                "interests",
                "hobbies",
                "sports",
                "education",
                "talking_goals",
            )
            if you_profile.get(k)
        }

    return {
        "person": {"name": name, "company": company},
        "contact": {
            k: contact.get(k)
            for k in ("linkedin_url", "email", "phone", "github_username")
            if contact.get(k)
        },
        "summary": {
            k: summary.get(k)
            for k in (
                "summary",
                "identity_confidence",
                "identity_notes",
                "career_history",
                "education",
                "personal_info",
                "public_presence",
                "senior_connections",
                "research_collaborators",
                "awards_and_recognitions",
                "notable_points",
            )
            if summary.get(k)
        },
        "conversation_ideas": {
            k: conversation.get(k)
            for k in (
                "conversation_brief",
                "talk_about",
                "openers",
                "deep_questions",
                "related_topics",
                "message_angle",
            )
            if conversation.get(k)
        },
        "public_profile": {
            k: public_profile.get(k)
            for k in (
                "headline",
                "location",
                "interests",
                "hobbies",
                "sports",
                "career_highlights",
                "summary_blurb",
            )
            if public_profile.get(k)
        },
        "linkedin_public": {
            "headline": li.get("headline"),
            "about": (li.get("about") or "")[:800] if li.get("about") else None,
            "url": li.get("url") or li.get("profile_url"),
        },
        "social": social,
        "you": you_slim,
    }
