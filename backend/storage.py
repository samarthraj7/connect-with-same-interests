import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Set, Tuple

PROFILES_DIR = Path(__file__).parent / "profiles"
INTERACTIONS_DIR = Path(__file__).parent / "interactions"

# gemini_search discovers LinkedIn / hints. LinkedIn deep-fetch shares gemini's
# TTL so a fresh gemini search doesn't re-hit LinkedIn immediately.
# Instagram / Facebook / Twitter are opt-in (--social) and cache independently —
# they must NOT piggyback on gemini freshness, or a default run would mark them
# "fresh" even when they never ran.
GEMINI_GROUP = {
    "gemini_search",
    "linkedin_public",
}
SOCIAL_SOURCES = frozenset({"instagram_public", "facebook_public", "twitter_public"})
INDEPENDENT_SOURCES = {
    "apollo",
    "github",
    "patents",
    "exa_search",
    "personal_info",
    "public_web",
} | set(SOCIAL_SOURCES)
ALL_SOURCES = INDEPENDENT_SOURCES | GEMINI_GROUP

# A source only counts as "checked" for caching purposes if it reached a real,
# settled answer — a transient error or a locally-skipped call (e.g. missing
# API key) shouldn't be cached, since we want the next run to just try again.
_SETTLED_STATUSES = {"ok", "not_found", "no_public_data", "blocked"}


class ProfileStore:
    """One JSON file per person under profiles/ — acts as a lightweight
    per-profile database. Re-running a search for the same person merges
    new source data in rather than overwriting: if a source succeeds this
    time, its data replaces the old snapshot; if a source fails or comes
    back empty this time but previously had good data, the old data is
    kept so a flaky API call never erases something we already found.

    Also tracks per-source fetch timestamps so callers can decide which
    connectors are still "fresh" and skip re-calling them — the main lever
    for cutting repeated API/token spend on repeat searches.

    Phase 3 CRM hooks: ``latest_common_ground``, ``contact``, and
    ``interactions/`` append-only logs live alongside the research snapshot.
    """

    def __init__(self, base_dir: Path = PROFILES_DIR, interactions_dir: Path = INTERACTIONS_DIR):
        self.base_dir = base_dir
        self.interactions_dir = interactions_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.interactions_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str, company: Optional[str] = None) -> Path:
        return self.base_dir / f"{_slugify(name, company)}.json"

    def load(self, name: str, company: Optional[str] = None) -> Optional[dict]:
        path = self.path_for(name, company)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            # A corrupted/empty cache file (e.g. an interrupted write) shouldn't crash
            # every future search for this person — treat it as no cache and move on.
            return None

    def freshness(self, name: str, company: Optional[str], ttl_hours: float) -> Tuple[Set[str], Set[str]]:
        """Returns (fresh, stale_or_missing) source-name sets for this profile."""
        existing = self.load(name, company)
        if not existing:
            return set(), set(ALL_SOURCES)

        fetched_at = existing.get("source_fetched_at", {})
        latest = existing.get("latest_sources", {})
        now = datetime.now(timezone.utc)

        def is_fresh(source: str) -> bool:
            ts = fetched_at.get(source)
            if not ts or source not in latest:
                return False
            return (now - datetime.fromisoformat(ts)) < timedelta(hours=ttl_hours)

        fresh: Set[str] = set()
        if is_fresh("gemini_search"):
            fresh |= GEMINI_GROUP
        for source in INDEPENDENT_SOURCES:
            if is_fresh(source):
                fresh.add(source)

        return fresh, ALL_SOURCES - fresh

    def save(
        self,
        name: str,
        company: Optional[str],
        merged_profile: dict,
        summary: dict,
        *,
        common_ground: Optional[dict] = None,
        usage: Optional[dict] = None,
    ) -> Path:
        path = self.path_for(name, company)
        existing = self.load(name, company) or {
            "name": name,
            "company": company,
            "created_at": merged_profile["fetched_at"],
            "search_history": [],
            "interactions": [],
            "contact": {},
        }

        existing["updated_at"] = merged_profile["fetched_at"]
        existing["latest_sources"] = _merge_sources(
            existing.get("latest_sources", {}), merged_profile["sources"]
        )
        existing["latest_source_status"] = {
            source: result.get("status") for source, result in existing["latest_sources"].items()
        }

        fetched_at_map = existing.get("source_fetched_at", {})
        for source_name, result in merged_profile["sources"].items():
            if result.get("status") in _SETTLED_STATUSES:
                fetched_at_map[source_name] = merged_profile["fetched_at"]
        existing["source_fetched_at"] = fetched_at_map

        existing["latest_summary"] = summary
        if merged_profile.get("knowledge_graph") is not None:
            existing["latest_knowledge_graph"] = merged_profile["knowledge_graph"]
            existing["latest_conflicts"] = merged_profile.get("conflicts") or []
        if common_ground is not None:
            existing["latest_common_ground"] = common_ground
        if usage is not None:
            existing["latest_usage"] = usage

        # Incremental freshness fingerprints + what's-new since last save
        try:
            from freshness import attach_fingerprints_to_record, compute_whats_new

            prev = dict(existing)
            changes = compute_whats_new(prev, merged_profile.get("sources") or {}, summary)
            if changes:
                existing["whats_new"] = {
                    "at": merged_profile["fetched_at"],
                    "changes": changes,
                }
                existing.setdefault("whats_new_history", []).append(existing["whats_new"])
                existing["whats_new_history"] = existing["whats_new_history"][-20:]
            attach_fingerprints_to_record(existing, existing.get("latest_sources") or {})
        except Exception:
            pass

        # Seed contact slots from research when empty (email/phone/linkedin later).
        # Canonical LinkedIn from the Find Me / research query always wins.
        contact = existing.setdefault("contact", {})
        preferred_li = None
        try:
            from identity_lock import normalize_linkedin_url

            preferred_li = normalize_linkedin_url(
                (merged_profile.get("query") or {}).get("linkedin_url")
            )
        except Exception:
            preferred_li = (merged_profile.get("query") or {}).get("linkedin_url")
        _seed_contact_from_sources(
            contact,
            existing.get("latest_sources") or {},
            preferred_linkedin=preferred_li,
        )

        history_entry: dict[str, Any] = {
            "fetched_at": merged_profile["fetched_at"],
            "source_status": merged_profile["source_status"],
        }
        if usage:
            history_entry["usage"] = usage
        existing.setdefault("search_history", []).append(history_entry)

        path.write_text(json.dumps(existing, indent=2))

        # Dual-write to Supabase when configured
        try:
            from db import upsert_person_snapshot

            upsert_person_snapshot(
                slug=path.stem,
                name=name,
                company=company,
                contact=contact,
                sources=existing.get("latest_sources") or {},
                summary=summary,
                conversation_engine=common_ground,
                fingerprints=existing.get("content_fingerprints"),
            )
        except Exception:
            pass
        return path

    def record_interaction(
        self,
        name: str,
        company: Optional[str],
        event: dict[str, Any],
    ) -> Path:
        """Append a CRM-style interaction (search, note, LinkedIn request, etc.)."""
        path = self.path_for(name, company)
        existing = self.load(name, company) or {
            "name": name,
            "company": company,
            "created_at": event.get("at") or datetime.now(timezone.utc).isoformat(),
            "search_history": [],
            "interactions": [],
            "contact": {},
        }
        entry = {
            "at": event.get("at") or datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in event.items() if k != "at"},
        }
        existing.setdefault("interactions", []).append(entry)
        existing["updated_at"] = entry["at"]
        path.write_text(json.dumps(existing, indent=2))

        # Also append to a flat interactions log for Phase 3 CRM views.
        log_path = self.interactions_dir / f"{_slugify(name, company)}.jsonl"
        with log_path.open("a") as fh:
            fh.write(json.dumps({"name": name, "company": company, **entry}) + "\n")
        return path


def _seed_contact_from_sources(contact: dict, sources: dict, *, preferred_linkedin: Optional[str] = None) -> None:
    """Fill missing contact fields from public research (never overwrite user edits).

    preferred_linkedin (canonical URL from Find Me / research query) always wins
    when contact.linkedin_url is empty — so Reach out never drops a picked profile.
    """
    if preferred_linkedin and not contact.get("linkedin_url"):
        contact["linkedin_url"] = preferred_linkedin
    apollo = sources.get("apollo") or {}
    if apollo.get("status") == "ok":
        if not contact.get("linkedin_url") and apollo.get("linkedin_url"):
            contact["linkedin_url"] = apollo["linkedin_url"]
        if not contact.get("email") and apollo.get("email"):
            contact["email"] = apollo["email"]
        if not contact.get("title") and apollo.get("title"):
            contact["title"] = apollo["title"]
    if not contact.get("linkedin_url"):
        for key in ("exa_search", "gemini_search", "linkedin_public"):
            src = sources.get(key) or {}
            url = src.get("linkedin_url") or (src.get("profile") or {}).get("url")
            if not url and key == "gemini_search":
                url = (src.get("social_profile_links") or {}).get("linkedin")
            if url:
                contact["linkedin_url"] = url
                break
    # Prefer canonical over rediscovered mismatches
    if preferred_linkedin:
        contact["linkedin_url"] = preferred_linkedin
    github = sources.get("github") or {}
    # Only auto-seed GitHub when identity_resolve confirmed the LinkedIn↔GitHub link
    # (or the username was explicitly user-supplied and still confirmed / attached).
    if not contact.get("github_username"):
        im = github.get("identity_match") or {}
        tier = (im.get("tier") or "").lower()
        username = github.get("username") or (github.get("profile") or {}).get("login")
        if github.get("status") == "ok" and username and (
            tier == "confirmed" or github.get("username_user_supplied")
        ):
            # user_supplied still requires confirmed when LinkedIn lock existed;
            # status=ok already encodes that when linkedin was present.
            contact["github_username"] = username
        elif github.get("status") == "ok" and username and not im:
            # Legacy path without resolver output — do not auto-merge on name
            pass


def _merge_sources(old_sources: dict, new_sources: dict) -> dict:
    merged = dict(old_sources)
    for source_name, new_result in new_sources.items():
        old_result = old_sources.get(source_name)
        if new_result.get("status") == "ok" or not old_result:
            merged[source_name] = new_result
        # else: new attempt failed/empty but we already had good data — keep it
    return merged


def _slugify(name: str, company: Optional[str]) -> str:
    base = f"{name}-{company}" if company else name
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return slug or "unknown"
