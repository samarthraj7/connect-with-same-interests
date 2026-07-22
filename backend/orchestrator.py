from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Optional

from connectors import (
    apollo,
    exa_search,
    facebook,
    gemini_search,
    github,
    instagram,
    linkedin_public,
    patents,
    personal_info,
    public_web,
    twitter,
)

ProgressCb = Optional[Callable[[str, float, str], None]]


@dataclass
class PersonQuery:
    name: str
    company: Optional[str] = None
    university: Optional[str] = None
    place: Optional[str] = None
    github_username: Optional[str] = None
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    domain: Optional[str] = None
    # From prior bad feedback — shapes Gemini/Exa/social queries on retry
    search_constraints: Optional[Dict[str, Any]] = None


def _emit(on_progress: ProgressCb, stage: str, progress: float, message: str) -> None:
    if not on_progress:
        return
    try:
        on_progress(stage, float(progress), message)
    except Exception as exc:
        print(f"  [orchestrator] on_progress error: {exc}", flush=True)


class SearchOrchestrator:
    """Fans out to connectors, then runs the deep agent + one-shot social discovery.

    Priority: Apollo → parallel Gemini/Exa/personal/public_web/GitHub → LinkedIn public
    → deep_agent (multi-hop) → social scrapers (1 Google attempt each via social_find).
    """

    def run(
        self,
        query: PersonQuery,
        skip: FrozenSet[str] = frozenset(),
        on_progress: ProgressCb = None,
    ) -> dict:
        results = {}

        # Wave 0 — licensed enrichment first
        _emit(on_progress, "enrichment", 0.08, "Enriching profile (Apollo / contact data)…")
        if "apollo" not in skip:
            results["apollo"] = apollo.enrich_person(
                name=query.name,
                company=query.company,
                domain=query.domain,
                linkedin_url=query.linkedin_url,
                email=query.email,
            )
            if results["apollo"].get("status") == "ok" and results["apollo"].get("linkedin_url"):
                if not query.linkedin_url:
                    query.linkedin_url = results["apollo"]["linkedin_url"]

        # Wave 0a — A-Leads contact email/phone (after identity hints; not for Find Me photos)
        if "aleads" not in skip:
            try:
                from connectors import aleads

                if aleads.configured():
                    print("  [orchestrator] aleads contact enrich…", flush=True)
                    results["aleads"] = aleads.enrich_contact(
                        name=query.name,
                        company=query.company,
                        domain=query.domain,
                        linkedin_url=query.linkedin_url,
                    )
                    al = results["aleads"]
                    if al.get("status") == "ok" and al.get("linkedin_url") and not query.linkedin_url:
                        query.linkedin_url = al["linkedin_url"]
            except Exception as exc:
                results["aleads"] = {"status": "error", "error": str(exc)[:200]}

        # Wave 0b — Enrich Layer profile when LinkedIn known (fills photo / title)
        if query.linkedin_url and "enrichlayer" not in skip:
            try:
                from connectors import enrichlayer

                if enrichlayer.configured():
                    el = enrichlayer.fetch_profile(query.linkedin_url, timeout=15)
                    results["enrichlayer"] = el
                    if el.get("status") == "ok":
                        if el.get("linkedin_url") and not query.linkedin_url:
                            query.linkedin_url = el["linkedin_url"]
            except Exception as exc:
                results["enrichlayer"] = {"status": "error", "error": str(exc)[:200]}

        _emit(on_progress, "enrichment", 0.18, "Licensed enrichment done")
        _emit(on_progress, "web_search", 0.22, "Searching public web, news, and LinkedIn…")

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {}
            if query.linkedin_url:
                print(f"  [orchestrator] identity lock linkedin={query.linkedin_url}", flush=True)
            if "github" not in skip:
                futures["github"] = pool.submit(
                    github.search_github,
                    name=query.name,
                    username=query.github_username,
                    company=query.company,
                    linkedin_url=query.linkedin_url,
                )
            if "patents" not in skip:
                futures["patents"] = pool.submit(patents.search_patents, name=query.name)
            sc = query.search_constraints or None
            if "gemini_search" not in skip:
                futures["gemini_search"] = pool.submit(
                    gemini_search.search_person,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                    linkedin_url=query.linkedin_url,
                    search_constraints=sc,
                )
            if "exa_search" not in skip:
                futures["exa_search"] = pool.submit(
                    exa_search.search_person_exa,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                    linkedin_url=query.linkedin_url,
                    search_constraints=sc,
                )
            if "personal_info" not in skip:
                futures["personal_info"] = pool.submit(
                    personal_info.search_personal_info,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                    linkedin_url=query.linkedin_url,
                )
            if "public_web" not in skip:
                futures["public_web"] = pool.submit(
                    public_web.search_public_presence,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    linkedin_url=query.linkedin_url,
                )
            for source, future in futures.items():
                results[source] = future.result()
        gemini_result = results.get("gemini_search")
        exa_result = results.get("exa_search")
        apollo_result = results.get("apollo") or {}
        if gemini_result is None and exa_result is None and apollo_result.get("status") != "ok":
            _emit(on_progress, "web_search", 0.45, "Web search finished (limited hits)")
            return results

        _emit(on_progress, "web_search", 0.48, "Web & personal research gathered")

        # Nimble: university/company pages about the person + follow bio links
        if "nimble_pages" not in skip:
            try:
                from connectors import nimble

                if nimble.configured():
                    _emit(
                        on_progress,
                        "nimble",
                        0.50,
                        "Extracting university/company pages (Nimble)…",
                    )
                    # Prefer company/university from query; fall back to Gemini/Apollo guesses
                    co = query.company or apollo_result.get("organization_name") or (
                        (gemini_result or {}).get("current_company")
                    )
                    uni = query.university
                    results["nimble_pages"] = nimble.enrich_person_pages(
                        name=query.name,
                        company=co,
                        university=uni,
                        linkedin_url=query.linkedin_url,
                        seed_sources=results,
                    )
                else:
                    results["nimble_pages"] = {
                        "status": "skipped",
                        "reason": "NIMBLE_API_KEY not set",
                        "pages": [],
                    }
            except Exception as exc:
                results["nimble_pages"] = {"status": "error", "error": str(exc)[:300], "pages": []}
                print(f"  [orchestrator] nimble error: {exc}", flush=True)

        social_links = dict((gemini_result or {}).get("social_profile_links") or {})
        linkedin_url = (
            query.linkedin_url
            or apollo_result.get("linkedin_url")
            or (exa_result or {}).get("linkedin_url")
            or social_links.get("linkedin")
        )

        if "linkedin_public" not in skip:
            results["linkedin_public"] = linkedin_public.fetch_linkedin_public(linkedin_url)

        # Wave — deep agent (after identity chosen + baseline connectors)
        _emit(on_progress, "deep_agent", 0.55, "Deep multi-hop research…")
        if "deep_agent" not in skip:
            try:
                from deep_agent import run_deep_agent

                print("  [orchestrator] deep_agent layer…", flush=True)
                deep = run_deep_agent(
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                    linkedin_url=linkedin_url or query.linkedin_url,
                    seed_sources=results,
                )
                results["deep_agent"] = deep
                # Merge discovered socials into links for scrapers
                for k, v in (deep.get("social_profile_links") or {}).items():
                    if v and not social_links.get(k):
                        social_links[k] = v
            except Exception as exc:
                results["deep_agent"] = {"status": "error", "error": str(exc)[:300]}
                print(f"  [orchestrator] deep_agent error: {exc}", flush=True)

        _emit(on_progress, "deep_agent", 0.68, "Deep research pass complete")

        linkedin_result = results.get("linkedin_public") or {}
        el_result = results.get("enrichlayer") if isinstance(results.get("enrichlayer"), dict) else {}
        photo_url = (
            apollo_result.get("photo_url")
            or el_result.get("profile_pic_url")
            or el_result.get("photo_url")
            or (el_result.get("profile") or {}).get("profile_pic_url")
            or linkedin_result.get("photo_url")
            or linkedin_result.get("profile_pic_url")
        )
        identity_hints = {
            "current_role": apollo_result.get("title")
            or (gemini_result or {}).get("current_role"),
            "bio_summary": (gemini_result or {}).get("bio_summary"),
            "linkedin_headline": linkedin_result.get("headline") or apollo_result.get("headline"),
            "linkedin_about": linkedin_result.get("about"),
            "photo_url": photo_url,
            "linkedin_photo_url": photo_url,
        }

        # Social scrapers — discovery already 1-attempt via social_find inside fetch_*
        _emit(on_progress, "socials", 0.72, "Matching public social profiles…")
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {}
            if "instagram_public" not in skip:
                futures["instagram_public"] = pool.submit(
                    instagram.fetch_instagram,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                    instagram_url=social_links.get("instagram"),
                    identity_hints=identity_hints,
                )
            if "facebook_public" not in skip:
                futures["facebook_public"] = pool.submit(
                    facebook.fetch_facebook,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                    facebook_url=social_links.get("facebook"),
                    identity_hints=identity_hints,
                )
            if "twitter_public" not in skip:
                futures["twitter_public"] = pool.submit(
                    twitter.fetch_twitter,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                    twitter_url=social_links.get("twitter")
                    or social_links.get("x")
                    or apollo_result.get("twitter_url"),
                    identity_hints=identity_hints,
                )
            for source, future in futures.items():
                results[source] = future.result()

        _emit(on_progress, "socials", 0.84, "Social discovery finished")
        return results
