"""Strict vs fuzzy person-name matching for Find Me disambiguation."""

from __future__ import annotations

import re
from typing import Any, Optional


def name_tokens(s: str) -> list[str]:
    return [
        t
        for t in re.findall(r"[a-z0-9]+", (s or "").lower())
        if len(t) > 1 and not re.fullmatch(r"[a-f0-9]{6,}", t)
    ]


def normalize_person_name(s: str) -> str:
    return " ".join(name_tokens(s))


def is_exact_name_match(query_name: str, candidate_name: str) -> bool:
    """True only when the candidate's display name is the same person-name as typed.

    Exact means:
    - normalized full-name equality, or
    - query has 2+ tokens, first+last match candidate first+last, and every
      query token appears in the candidate name.
    """
    q = name_tokens(query_name)
    c = name_tokens(candidate_name)
    if not q or not c:
        return False
    if " ".join(q) == " ".join(c):
        return True
    if len(q) < 2 or len(c) < 2:
        return False
    if q[0] != c[0] or q[-1] != c[-1]:
        return False
    cset = set(c)
    return all(t in cset for t in q)


def partition_candidates(
    query_name: str,
    candidates: list,
    *,
    score_key: str = "_score",
    probable_min_score: float = 0.25,
) -> dict[str, Any]:
    """Split Find Me hits into exact vs probable lists.

    Returns:
      exact, probable, match_mode ("exact" | "probable_only" | "none"),
      and candidates (exact if any else probable) for backward compatibility.
    """
    exact: list = []
    probable: list = []

    for raw in candidates or []:
        if not isinstance(raw, dict):
            continue
        c = dict(raw)
        score = c.pop(score_key, None)
        # Passthrough / empty-search cards are neither exact nor probable people
        ctx = (c.get("context") or "").lower()
        if "no linkedin people matches" in ctx or "no public matches" in ctx:
            continue
        name = c.get("name") or ""
        if is_exact_name_match(query_name, name):
            exact.append(c)
        else:
            if score is None or float(score) >= probable_min_score:
                probable.append(c)
            elif c.get("linkedin_url"):
                probable.append(c)

    def _rank(c: dict) -> tuple:
        # Prefer rows that still carry a private score if present was stripped;
        # fall back to having photo + linkedin.
        return (
            1 if (c.get("photo_url") or "").startswith("http") else 0,
            1 if c.get("linkedin_url") else 0,
            1 if c.get("company") else 0,
        )

    exact = sorted(exact, key=_rank, reverse=True)
    probable = sorted(probable, key=_rank, reverse=True)

    if exact:
        mode = "exact"
        display = exact
    elif probable:
        mode = "probable_only"
        display = probable
    else:
        mode = "none"
        display = []

    return {
        "exact": exact,
        "probable": probable,
        "candidates": display,
        "match_mode": mode,
    }


def exact_match_message(mode: str) -> Optional[str]:
    if mode == "probable_only":
        return (
            "Exact match not found. These are the probable people that you might be looking for."
        )
    if mode == "exact":
        return None
    return "No people matches found for this name."
