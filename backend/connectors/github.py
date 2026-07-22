import os
from typing import List, Optional

import requests

GITHUB_REST_API = "https://api.github.com"
GITHUB_GRAPHQL_API = "https://api.github.com/graphql"

GRAPHQL_USER_QUERY = """
query($login: String!) {
  user(login: $login) {
    socialAccounts(first: 10) {
      nodes { provider displayName url }
    }
    organizations(first: 10) {
      nodes { login name }
    }
    contributionsCollection {
      contributionCalendar { totalContributions }
      totalCommitContributions
      totalPullRequestContributions
    }
  }
}
"""


def search_github(
    name: str,
    username: Optional[str] = None,
    company: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> dict:
    """Look up a person on GitHub.

    If `username` is known, fetch it directly. Otherwise search by display name.
    When `linkedin_url` is provided, candidates are scored via identity_resolve
    and only a *confirmed* match is returned as status=ok (name-only never merges).
    """
    try:
        if username:
            user = _fetch_user(username)
            if not user:
                return {"status": "not_found", "candidates": []}
            result = _pack_user(username, user)
            if linkedin_url:
                result = _attach_identity_resolution(
                    result,
                    name=name,
                    company=company,
                    linkedin_url=linkedin_url,
                    user_supplied_username=True,
                )
            return result

        # `in:fullname` restricts matching to the display-name field — without it,
        # GitHub's search also matches bio/email text, which turns up unrelated
        # accounts that merely mention the person's name (e.g. in a bio rant).
        q = f"{name} in:fullname"
        if company:
            # Soft hint only — GitHub search company filter is unreliable
            q = f"{name} {company} in:fullname"
        resp = requests.get(
            f"{GITHUB_REST_API}/search/users",
            params={"q": q, "per_page": 5},
            headers=_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return {"status": "not_found", "candidates": []}

        name_matched = []
        for item in items:
            candidate = _fetch_user(item["login"])
            if candidate and _name_matches(candidate.get("name"), name):
                packed = _pack_user(item["login"], candidate)
                name_matched.append(packed)

        if not name_matched:
            return {
                "status": "not_found",
                "reason": "no candidate's display name matched closely enough",
                "candidates": [i["login"] for i in items],
            }

        # With LinkedIn: score all name-matched candidates; never auto-merge on name alone
        if linkedin_url:
            return _resolve_among_candidates(
                name_matched,
                name=name,
                company=company,
                linkedin_url=linkedin_url,
            )

        # No LinkedIn lock — return ambiguous name matches (do NOT pretend confirmed)
        top = name_matched[0]
        top["status"] = "ambiguous"
        top["reason"] = "name_match_only_without_linkedin_lock"
        top["candidates"] = [
            {
                "login": (c.get("profile") or {}).get("login"),
                "html_url": (c.get("profile") or {}).get("html_url"),
                "name": (c.get("profile") or {}).get("name"),
            }
            for c in name_matched
        ]
        top["identity_match"] = {
            "linkedin_url": None,
            "candidate_url": (top.get("profile") or {}).get("html_url"),
            "score": 0.0,
            "tier": "no_match",
            "evidence": [
                "Name-only GitHub match — LinkedIn URL required before treating as same person"
            ],
        }
        try:
            from identity_resolve import log_decision

            log_decision(top["identity_match"], reason="github_name_only_no_linkedin")
        except Exception:
            pass
        return top
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)}


def _pack_user(username: str, user: dict) -> dict:
    result = {
        "status": "ok",
        "username": username,
        "profile": user,
        "repos": _fetch_top_repos(username),
    }
    graph = _fetch_graphql(username)
    if graph is not None:
        result["social_accounts"] = graph.get("social_accounts", [])
        result["organizations"] = graph.get("organizations", [])
        result["contributions"] = graph.get("contributions")
    else:
        result["social_accounts_note"] = "GITHUB_TOKEN not set — GraphQL social graph skipped"
    return result


def _attach_identity_resolution(
    result: dict,
    *,
    name: Optional[str],
    company: Optional[str],
    linkedin_url: str,
    user_supplied_username: bool = False,
) -> dict:
    from identity_resolve import TIER_CONFIRMED, public_resolution, resolve_linkedin_github

    profile = result.get("profile") or {}
    resolution = resolve_linkedin_github(
        linkedin_url=linkedin_url,
        github_username=profile.get("login") or result.get("username"),
        name=name,
        company=company,
    )
    result["identity_match"] = public_resolution(resolution)
    result["username_user_supplied"] = user_supplied_username
    if resolution.get("tier") == TIER_CONFIRMED:
        result["status"] = "ok"
    else:
        # Keep profile data for UI/HITL but do not treat as merged identity
        result["status"] = "ambiguous"
        result["reason"] = f"identity_resolve:{resolution.get('tier')}"
    return result


def _resolve_among_candidates(
    packed_candidates: list,
    *,
    name: Optional[str],
    company: Optional[str],
    linkedin_url: str,
) -> dict:
    from identity_resolve import (
        TIER_CONFIRMED,
        TIER_POSSIBLE,
        public_resolution,
        resolve_linkedin_github,
    )

    scored = []
    for packed in packed_candidates:
        profile = packed.get("profile") or {}
        login = profile.get("login") or packed.get("username")
        resolution = resolve_linkedin_github(
            linkedin_url=linkedin_url,
            github_username=login,
            name=name,
            company=company,
        )
        pub = public_resolution(resolution)
        scored.append((pub, packed))

    scored.sort(key=lambda x: -float(x[0].get("score") or 0))
    best_res, best_packed = scored[0]
    out = dict(best_packed)
    out["identity_match"] = best_res
    out["identity_candidates"] = [public_resolution(r) for r, _ in scored]
    out["candidates"] = [
        {
            "login": (p.get("profile") or {}).get("login"),
            "html_url": (p.get("profile") or {}).get("html_url"),
            "identity_match": public_resolution(r),
        }
        for r, p in scored
    ]

    if best_res.get("tier") == TIER_CONFIRMED:
        out["status"] = "ok"
        out["username"] = (best_packed.get("profile") or {}).get("login")
        return out

    out["status"] = "ambiguous"
    out["reason"] = f"identity_resolve:{best_res.get('tier')}"
    if best_res.get("tier") == TIER_POSSIBLE:
        out["needs_human_review"] = True
    return out


def _name_matches(candidate_name: Optional[str], query_name: str) -> bool:
    """Every word in the searched name must appear in the candidate's display
    name — a cheap guard against false-positive bio/email text matches."""
    if not candidate_name:
        return False
    query_words = set(query_name.lower().split())
    candidate_words = set(candidate_name.lower().split())
    return query_words.issubset(candidate_words)


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_user(username: str) -> Optional[dict]:
    resp = requests.get(f"{GITHUB_REST_API}/users/{username}", headers=_headers(), timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return {
        "login": data.get("login"),
        "name": data.get("name"),
        "bio": data.get("bio"),
        "company": data.get("company"),
        "location": data.get("location"),
        "blog": data.get("blog"),
        "email": data.get("email"),
        "twitter_username": data.get("twitter_username"),
        "public_repos": data.get("public_repos"),
        "followers": data.get("followers"),
        "html_url": data.get("html_url"),
        "created_at": data.get("created_at"),
    }


def _fetch_top_repos(username: str, limit: int = 5) -> List[dict]:
    resp = requests.get(
        f"{GITHUB_REST_API}/users/{username}/repos",
        params={"sort": "updated", "per_page": limit},
        headers=_headers(),
        timeout=10,
    )
    if resp.status_code != 200:
        return []
    return [
        {
            "name": r.get("name"),
            "description": r.get("description"),
            "language": r.get("language"),
            "stars": r.get("stargazers_count"),
            "url": r.get("html_url"),
            "topics": r.get("topics", []),
        }
        for r in resp.json()
    ]


def _fetch_graphql(username: str) -> Optional[dict]:
    """GraphQL requires auth even for public data — skip cleanly if no token."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None

    resp = requests.post(
        GITHUB_GRAPHQL_API,
        json={"query": GRAPHQL_USER_QUERY, "variables": {"login": username}},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        return None

    user = (resp.json().get("data") or {}).get("user")
    if not user:
        return None

    contributions = user.get("contributionsCollection", {})
    return {
        "social_accounts": [
            {"provider": n.get("provider"), "handle": n.get("displayName"), "url": n.get("url")}
            for n in (user.get("socialAccounts") or {}).get("nodes", [])
        ],
        "organizations": [
            n.get("name") or n.get("login") for n in (user.get("organizations") or {}).get("nodes", [])
        ],
        "contributions": {
            "total_last_year": (contributions.get("contributionCalendar") or {}).get("totalContributions"),
            "total_commits": contributions.get("totalCommitContributions"),
            "total_pull_requests": contributions.get("totalPullRequestContributions"),
        },
    }
