import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from common_ground import (
    TOKEN_COST_BASIC,
    TOKEN_COST_DETAILED,
    analyze_common_ground,
    apply_overlap_to_summary,
)
from connectors import gemini_search
from merge import merge_profile
from orchestrator import PersonQuery, SearchOrchestrator
from storage import SOCIAL_SOURCES, ProfileStore
from synthesize import summarize_profile
from user_profile import default_path, load_user_profile

load_dotenv()


def choose_candidate(name: str) -> Optional[dict]:
    """Runs the disambiguation search and lets the user pick which person
    they mean before the expensive deep dive runs. Returns None (search
    broadly, no pre-fill) if there's nothing to disambiguate or the user
    skips it."""
    print(f"\nSearching for people named '{name}' ...")
    result = gemini_search.find_candidates(name)

    if result.get("status") != "ok" or not result.get("candidates"):
        print("  No distinct candidates surfaced — proceeding with a general search.")
        return None

    candidates = result["candidates"]
    print(f"\nFound {len(candidates)} possible match(es):")
    for i, c in enumerate(candidates, 1):
        role = c.get("role") or "role unknown"
        company = c.get("company") or "company unknown"
        location = f" — {c['location']}" if c.get("location") else ""
        link = f"\n     {c['linkedin_url']}" if c.get("linkedin_url") else ""
        print(f"  {i}. {c['name']} — {role} at {company}{location}{link}")

    choice = input(f"\nChoose a person [1-{len(candidates)}], or press enter to search broadly: ").strip()
    if not choice:
        return None
    try:
        return candidates[int(choice) - 1]
    except (ValueError, IndexError):
        print("  Invalid choice — proceeding with a general search.")
        return None


def run_search(
    name: str,
    company: Optional[str],
    university: Optional[str],
    place: Optional[str],
    github_username: Optional[str],
    linkedin_url: Optional[str],
    force_refresh: bool,
    tier: str = "detailed",
    fetch_social: bool = False,
) -> None:
    orchestrator = SearchOrchestrator()
    store = ProfileStore()
    existing = store.load(name, company)

    ttl_hours = float(os.environ.get("CACHE_TTL_HOURS", "24"))
    if force_refresh:
        fresh: set = set()
    else:
        fresh, _ = store.freshness(name, company, ttl_hours)

    # Instagram / Facebook / Twitter are opt-in — skip unless --social.
    skip = set(fresh)
    if not fetch_social:
        skip |= set(SOCIAL_SOURCES)

    header = f"\nDeep dive: '{name}'" + (f" @ {company}" if company else "") + " ..."
    print(header)
    print(f"  tier: {tier} (basic={TOKEN_COST_BASIC} token · detailed={TOKEN_COST_DETAILED} tokens)")
    print(f"  social (IG/FB/X): {'on' if fetch_social else 'off (pass --social to enable)'}")
    if fresh:
        print(f"  cached (fresh within {ttl_hours}h, --force-refresh to bypass): {', '.join(sorted(fresh))}")

    raw_results = orchestrator.run(
        PersonQuery(
            name=name,
            company=company,
            university=university,
            place=place,
            github_username=github_username,
            linkedin_url=linkedin_url,
        ),
        skip=frozenset(skip),
    )

    for source, result in raw_results.items():
        detail = result.get("reason") or result.get("error") or ""
        suffix = f" ({detail})" if detail else ""
        print(f"  [{source}] {result.get('status')}{suffix}")
    for source in sorted(fresh):
        if source in SOCIAL_SOURCES and not fetch_social:
            continue
        print(f"  [{source}] cached")
    if not fetch_social:
        for source in sorted(SOCIAL_SOURCES):
            print(f"  [{source}] skipped")

    # `merged` only carries freshly-fetched sources — that's what storage.save()
    # merges into the cache and stamps with a new fetch time. The LLM, though,
    # needs the FULL picture (fresh + cached) to write a coherent summary.
    merged = merge_profile(
        query={
            "name": name,
            "company": company,
            "university": university,
            "place": place,
            "github_username": github_username,
            "linkedin_url": linkedin_url,
        },
        raw_results=raw_results,
    )

    # Only worth re-summarizing if a freshly-fetched source actually found something
    # new — a locally-skipped connector (e.g. no PatentsView key) re-attempting every
    # run and coming back empty shouldn't be enough to trigger a fresh Gemini call.
    has_new_data = any(result.get("status") == "ok" for result in raw_results.values())
    if not has_new_data and existing and existing.get("latest_summary"):
        print("\nNothing new found — skipping the Gemini call entirely, reusing cached summary.")
        summary = existing["latest_summary"]
    else:
        cached_sources = (existing or {}).get("latest_sources", {})
        llm_view = copy.deepcopy(merged)
        llm_view["sources"] = {**cached_sources, **raw_results}
        print("\nSummarizing with Gemini...")
        summary = summarize_profile(llm_view)
        if summary.get("status") != "ok":
            err = summary.get("error") or summary.get("reason") or summary.get("status")
            print(f"  summarize failed: {err}")
            # Fall back to cached briefing so detailed common-ground can still run.
            cached_summary = (existing or {}).get("latest_summary")
            if isinstance(cached_summary, dict) and cached_summary.get("status") == "ok":
                print("  reusing previous cached summary for common ground")
                summary = cached_summary
            else:
                print("  no usable cached summary — common ground may be skipped")

    cached_sources = (existing or {}).get("latest_sources", {})
    all_sources = {**cached_sources, **raw_results}

    overlap = None
    usage = {"tier": "basic", "tokens_charged": TOKEN_COST_BASIC}
    if tier == "detailed" and summary.get("status") == "ok":
        print("\nFinding common ground with your profile "
              f"({default_path().name})...")
        try:
            user = load_user_profile()
            print(f"  you: {user.get('name') or '(unnamed)'}")
            if (user.get("crm") or {}).get("source") == "research_dump":
                print("  (normalized research dump → YOU profile)")
        except Exception as exc:
            user = None
            print(f"  (could not load user profile: {exc})")
        overlap = analyze_common_ground(
            summary,
            them_name=name,
            user_profile=user,
            them_sources=all_sources,
        )
        if overlap.get("status") == "ok":
            summary = apply_overlap_to_summary(summary, overlap)
            usage = {
                "tier": "detailed",
                "tokens_charged": TOKEN_COST_DETAILED,
                "overlap_score": overlap.get("overlap_score"),
            }
            print(f"  overlap score: {overlap.get('overlap_score')}")
        else:
            detail = overlap.get("reason") or overlap.get("error") or overlap.get("status")
            print(f"  common ground skipped/failed: {detail}")
            usage["note"] = detail
    elif tier == "detailed":
        reason = summary.get("error") or summary.get("reason") or "summary not ok"
        print(f"\nSkipping common ground — need a usable person summary first ({reason}).")
        overlap = {"status": "skipped", "reason": f"person summary unavailable: {reason}"}
    elif tier == "basic":
        print("\nSkipping common ground (--tier basic). Icebreakers stay empty until detailed.")

    path = store.save(
        name,
        company,
        merged,
        summary,
        common_ground=overlap,
        usage=usage,
    )
    store.record_interaction(
        name,
        company,
        {
            "type": "research",
            "tier": usage.get("tier"),
            "tokens_charged": usage.get("tokens_charged"),
            "overlap_score": usage.get("overlap_score"),
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )
    print(f"Saved profile -> {path}")
    print(f"Usage: {usage.get('tier')} · {usage.get('tokens_charged')} token(s)")

    print("\n--- Summary ---")
    # Keep the main briefing readable: print core fields without the sections
    # that get their own banners below.
    core = {
        k: v
        for k, v in summary.items()
        if k not in (
            "public_presence",
            "senior_connections",
            "personal_info",
            "common_ground",
            "conversation_starters",
            "deep_dive_questions",
        )
    }
    print(json.dumps(core, indent=2))
    _print_common_ground(summary.get("common_ground") or overlap)
    _print_questions(
        summary.get("conversation_starters"),
        summary.get("deep_dive_questions"),
    )
    _print_personal_info(
        summary.get("personal_info"),
        raw_results.get("personal_info") or cached_sources.get("personal_info"),
    )
    if fetch_social:
        _print_social("Instagram", raw_results.get("instagram_public") or cached_sources.get("instagram_public"))
        _print_social("Facebook", raw_results.get("facebook_public") or cached_sources.get("facebook_public"))
        _print_social("Twitter/X", raw_results.get("twitter_public") or cached_sources.get("twitter_public"))
    _print_public_presence(summary.get("public_presence"))
    _print_senior_connections(summary.get("senior_connections"))


def _print_common_ground(section) -> None:
    print("\n=== Common ground (you ↔ them) ===")
    if not isinstance(section, dict):
        print("  (not computed — summary may have failed, or --tier was not detailed)")
        return
    if section.get("status") and section.get("status") != "ok":
        detail = section.get("reason") or section.get("error") or section.get("status")
        print(f"  status: {detail}")
        return

    score = section.get("overlap_score")
    you = section.get("you_name")
    if you:
        print(f"  you: {you}")
    if score is not None:
        print(f"  overlap score: {score}/100")
    summary = section.get("overlap_summary")
    if summary:
        print(f"  {summary}")

    grounds = section.get("common_grounds") or []
    if grounds:
        print("\n  Shared / closely related points:")
        for item in grounds:
            if not isinstance(item, dict):
                print(f"    • {item}")
                continue
            point = item.get("point") or "(untitled)"
            strength = item.get("strength") or ""
            tag = f" [{strength}]" if strength else ""
            print(f"    • {point}{tag}")
            if item.get("you_side"):
                print(f"        you:  {item['you_side']}")
            if item.get("them_side"):
                print(f"        them: {item['them_side']}")
            if item.get("why_it_matters"):
                print(f"        why:  {item['why_it_matters']}")
    else:
        print("\n  Shared points: (none found from current profiles)")

    topics = section.get("related_topics_to_discuss") or []
    if topics:
        print("\n  Related topics to discuss:")
        for topic in topics:
            print(f"    • {topic}")

    angle = section.get("outreach_angle")
    if angle:
        print(f"\n  Outreach angle: {angle}")

    gaps = section.get("your_profile_gaps") or []
    if gaps:
        print("\n  Improve your profile for better overlap next time:")
        for gap in gaps:
            print(f"    • {gap}")


def _print_questions(starters, deep_dives) -> None:
    print("\n=== Conversation starters (from common ground) ===")
    if starters:
        for q in starters:
            print(f"  • {q}")
    else:
        print("  (none — run --tier detailed after filling user_profile.json)")

    print("\n=== Deep-dive questions (from common ground) ===")
    if deep_dives:
        for q in deep_dives:
            print(f"  • {q}")
    else:
        print("  (none — run --tier detailed after filling user_profile.json)")


def _print_personal_info(section, raw_source=None) -> None:
    print("\n=== Personal info ===")
    data = section if isinstance(section, dict) else {}
    # Fall back to raw connector fields if summary omitted the section
    if not data and isinstance(raw_source, dict) and raw_source.get("status") == "ok":
        data = raw_source

    if not data or not any(
        data.get(k)
        for k in (
            "born_or_hometown",
            "raised_in",
            "current_location",
            "lived_in",
            "hobbies",
            "sports_interests",
            "weekend_preferences",
            "family_background",
            "personal_notes",
            "birthplace_note",
            "milestone_answers",
        )
    ):
        status = (raw_source or {}).get("status") if isinstance(raw_source, dict) else None
        print(f"  (nothing public found{f' — {status}' if status else ''})")
        return

    def line(label: str, value) -> None:
        if not value:
            return
        if isinstance(value, list):
            print(f"  {label}:")
            for item in value:
                print(f"    • {item}")
        else:
            print(f"  {label}: {value}")

    line("Born / hometown", data.get("born_or_hometown"))
    if data.get("birthplace_note") and not data.get("born_or_hometown"):
        print(f"  Birthplace: {data['birthplace_note']}")
    line("Raised in", data.get("raised_in"))
    line("Lives now", data.get("current_location"))
    line("Also lived in", data.get("lived_in"))
    line("Hobbies", data.get("hobbies"))
    line("Sports", data.get("sports_interests"))
    line("Weekends", data.get("weekend_preferences"))
    line("Family", data.get("family_background"))
    line("Notes", data.get("personal_notes"))

    # Show milestone Q&A when present (from connector or summary passthrough)
    milestones = data.get("milestone_answers") or (raw_source or {}).get("milestone_answers") or {}
    if isinstance(milestones, dict) and milestones:
        print("  Milestone answers:")
        for key, ans in milestones.items():
            if not isinstance(ans, dict):
                continue
            exact = ans.get("exact_found")
            direct = ans.get("direct_answer")
            closest = ans.get("closest_verified_context")
            if not direct and not closest:
                continue
            tag = "exact" if exact else "not public — closest context"
            print(f"    • [{key}] ({tag})")
            if direct:
                print(f"      {direct}")
            if closest and not exact:
                print(f"      closest verified: {closest}")

    evidence = data.get("evidence") or []
    if evidence:
        print("  Evidence:")
        for item in evidence[:8]:
            fact = item.get("fact") if isinstance(item, dict) else str(item)
            hint = item.get("source_hint") if isinstance(item, dict) else None
            suffix = f"  [{hint}]" if hint else ""
            print(f"    • {fact}{suffix}")


def _print_social(label: str, data) -> None:
    print(f"\n=== {label} ===")
    if not isinstance(data, dict):
        print("  (no data)")
        return
    status = data.get("status")
    confidence = data.get("match_confidence")
    score = data.get("match_score")
    notes = data.get("match_notes") or []
    discovery = data.get("discovery") or {}

    if discovery.get("query"):
        print(f"  google query: {discovery.get('query')}")
    if discovery.get("method"):
        print(f"  discovery: {discovery.get('method')}")

    if status == "ambiguous":
        handle = data.get("handle")
        print("  status: ambiguous (not confirmed as same person)")
        if handle:
            print(f"  best candidate: @{handle}  {data.get('profile_url') or ''}".rstrip())
        elif data.get("profile_url"):
            print(f"  best candidate: {data.get('profile_url')}")
        if confidence or score is not None:
            print(f"  match: confidence={confidence or 'n/a'}  score={score if score is not None else 'n/a'}")
        for note in notes[:4]:
            print(f"  • {note}")
        return

    if status != "ok":
        detail = data.get("reason") or data.get("error") or status
        print(f"  status: {detail}")
        if confidence:
            print(f"  match_confidence: {confidence}")
        return

    profile = data.get("profile") or {}
    handle = data.get("handle") or profile.get("username")
    if handle:
        print(f"  @{handle}  {data.get('profile_url') or ''}".rstrip())
    elif data.get("profile_url"):
        print(f"  {data.get('profile_url')}")
    if confidence or score is not None:
        print(f"  match: confidence={confidence or 'n/a'}  score={score if score is not None else 'n/a'}")
    for note in notes[:3]:
        print(f"  • {note}")
    display_name = profile.get("full_name") or profile.get("name")
    if display_name:
        print(f"  name: {display_name}")
    bio = profile.get("biography") or profile.get("bio")
    if bio:
        print(f"  bio: {bio}")
    bits = []
    followers = profile.get("followers") or profile.get("followers_count")
    if followers is not None:
        bits.append(f"followers={followers}")
    if profile.get("following") is not None:
        bits.append(f"following={profile['following']}")
    if profile.get("media_count") is not None:
        bits.append(f"posts={profile['media_count']}")
    if profile.get("statuses_count") is not None:
        bits.append(f"tweets={profile['statuses_count']}")
    if bits:
        print(f"  {' · '.join(bits)}")

    posts = data.get("recent_posts") or []
    apidirect = ((data.get("apidirect_posts") or {}).get("posts") if isinstance(data.get("apidirect_posts"), dict) else None) or []
    show = posts or apidirect
    if show:
        print("  recent posts:")
        for post in show[:5]:
            caption = post.get("caption") or post.get("snippet") or "(no caption)"
            print(f"    • {caption[:160]}")
    else:
        print("  recent posts: (none returned)")


def _print_public_presence(presence) -> None:
    print("\n=== Public posts & engagement ===")
    if not isinstance(presence, dict):
        print("  (no public-presence section in summary)")
        return

    posts_about = presence.get("posts_about") or []
    recent = presence.get("recent_posts_or_writing") or []
    liked = presence.get("liked_or_engaged_with") or []
    note = presence.get("availability_note")

    if posts_about:
        print("\nWhat their posts / writing are about:")
        for theme in posts_about:
            print(f"  • {theme}")
    else:
        print("\nWhat their posts / writing are about: (nothing public found)")

    if recent:
        print("\nRecent public posts / writing:")
        for item in recent:
            topic = item.get("topic") or "(untitled)"
            source = item.get("source")
            snippet = item.get("snippet")
            line = f"  • {topic}"
            if source:
                line += f"  [{source}]"
            print(line)
            if snippet:
                print(f"      {snippet}")
    else:
        print("\nRecent public posts / writing: (none found)")

    if liked:
        print("\nPublicly liked / engaged with:")
        for item in liked:
            topic = item.get("topic") or "(unknown)"
            evidence = item.get("evidence")
            print(f"  • {topic}")
            if evidence:
                print(f"      evidence: {evidence}")
    else:
        print("\nPublicly liked / engaged with: (none evidenced)")

    if note:
        print(f"\nNote: {note}")


def _print_senior_connections(connections) -> None:
    print("\n=== Senior / high-level connections ===")
    if not connections:
        print("  (none publicly named — LinkedIn connections list is not available)")
        return

    for person in connections:
        name = person.get("name") or "(unknown)"
        title = person.get("title")
        seniority = person.get("seniority")
        context = person.get("context")
        header = name
        if title:
            header += f" — {title}"
        if seniority:
            header += f" ({seniority})"
        print(f"  • {header}")
        if context:
            print(f"      {context}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect Deeply — terminal profile search")
    parser.add_argument("--name", help="Full name of the person to search for")
    parser.add_argument(
        "--company", help="Skip disambiguation and search directly with this company hint"
    )
    parser.add_argument(
        "--university", help="Skip disambiguation and search directly with this university hint"
    )
    parser.add_argument(
        "--place", help="Skip disambiguation and search directly with this location hint"
    )
    parser.add_argument(
        "--github", dest="github_username", help="Known GitHub username (optional, skips search)"
    )
    parser.add_argument(
        "--linkedin", dest="linkedin_url", help="Known LinkedIn profile URL — skips disambiguation entirely"
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Bypass the cache entirely and re-run every connector + the Gemini summary",
    )
    parser.add_argument(
        "--tier",
        choices=("basic", "detailed"),
        default="detailed",
        help=(
            "basic = research briefing only (1 token); "
            "detailed = research + common-ground overlap & questions (3 tokens). "
            "Default: detailed."
        ),
    )
    parser.add_argument(
        "--social",
        action="store_true",
        help="Also fetch Instagram / Facebook / Twitter (off by default — slower + extra API cost)",
    )
    parser.add_argument(
        "--user-profile",
        dest="user_profile_path",
        help="Path to your user_profile.json (default: backend/user_profile.json)",
    )
    args = parser.parse_args()

    if args.user_profile_path:
        os.environ["CONNECT_DEEPLY_USER_PROFILE"] = args.user_profile_path

    name = args.name or input("Name to search for: ").strip()
    if not name:
        print("A name is required.")
        sys.exit(1)

    company, university, place, linkedin_url = args.company, args.university, args.place, args.linkedin_url

    # If any hint was already given directly, the person is already disambiguated —
    # don't make them pick from a candidate list too.
    already_disambiguated = any([company, university, place, linkedin_url])
    if not already_disambiguated:
        candidate = choose_candidate(name)
        if candidate:
            name = candidate.get("name") or name
            company = candidate.get("company")
            place = candidate.get("location")
            linkedin_url = candidate.get("linkedin_url")

    run_search(
        name,
        company,
        university,
        place,
        args.github_username,
        linkedin_url,
        args.force_refresh,
        tier=args.tier,
        fetch_social=args.social,
    )


if __name__ == "__main__":
    main()
