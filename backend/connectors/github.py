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


def search_github(name: str, username: Optional[str] = None, company: Optional[str] = None) -> dict:
    """Look up a person on GitHub. If `username` is already known, fetch it
    directly (cheap, no rate-limited search call). Otherwise search by name,
    optionally narrowed by company, and take the top match. When a
    GITHUB_TOKEN is configured, also pulls the GraphQL "social graph" —
    self-disclosed social handles, org memberships, and contribution stats —
    which the REST API doesn't expose."""
    try:
        if username:
            user = _fetch_user(username)
            if not user:
                return {"status": "not_found", "candidates": []}
        else:
            # `in:fullname` restricts matching to the display-name field — without it,
            # GitHub's search also matches bio/email text, which turns up unrelated
            # accounts that merely mention the person's name (e.g. in a bio rant).
            resp = requests.get(
                f"{GITHUB_REST_API}/search/users",
                params={"q": f"{name} in:fullname", "per_page": 5},
                headers=_headers(),
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            if not items:
                return {"status": "not_found", "candidates": []}

            username, user = None, None
            for item in items:
                candidate = _fetch_user(item["login"])
                if candidate and _name_matches(candidate.get("name"), name):
                    username, user = item["login"], candidate
                    break

            if username is None:
                return {
                    "status": "not_found",
                    "reason": "no candidate's display name matched closely enough",
                    "candidates": [i["login"] for i in items],
                }

        result = {
            "status": "ok",
            "profile": user,
            "repos": _fetch_top_repos(username),
        }
        if not (username and result.get("profile")):
            return result

        graph = _fetch_graphql(username)
        if graph is not None:
            result["social_accounts"] = graph.get("social_accounts", [])
            result["organizations"] = graph.get("organizations", [])
            result["contributions"] = graph.get("contributions")
        else:
            result["social_accounts_note"] = "GITHUB_TOKEN not set — GraphQL social graph skipped"

        return result
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)}


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
