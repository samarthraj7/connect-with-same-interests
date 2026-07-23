"""Research coordinator: identity gate, then parallel task orchestrators.

Task groups:
  IdentityOrchestrator  — licensed enrichment (sequential); never auto-locks LinkedIn
  WebOrchestrator       — Gemini / Exa / public_web / patents / GitHub
  PersonalOrchestrator  — family / place / age milestones
  NimbleOrchestrator    — university/company page extracts (+ optional seed hop)
  DeepOrchestrator      — multi-hop public research
  SocialOrchestrator    — IG / FB / X in parallel

Synthesize + common-ground stay in the API layer after identity_filter.
"""

from __future__ import annotations

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
    search_constraints: Optional[Dict[str, Any]] = None
    # True when caller already locked identity (Find Me / draft). Enrichment must NOT override.
    linkedin_locked: bool = False


def _emit(on_progress: ProgressCb, stage: str, progress: float, message: str) -> None:
    if not on_progress:
        return
    try:
        on_progress(stage, float(progress), message)
    except Exception as exc:
        print(f"  [orchestrator] on_progress error: {exc}", flush=True)


class IdentityOrchestrator:
    """Licensed enrichment. Suggests LinkedIn but never locks it unless already locked."""

    def run(self, query: PersonQuery, skip: FrozenSet[str]) -> dict:
        results: dict = {}
        if "apollo" not in skip:
            results["apollo"] = apollo.enrich_person(
                name=query.name,
                company=query.company,
                domain=query.domain,
                linkedin_url=query.linkedin_url,
                email=query.email,
            )
            sug = (results["apollo"] or {}).get("linkedin_url")
            if sug and not query.linkedin_url:
                results["apollo"]["suggested_linkedin_url"] = sug

        if "aleads" not in skip:
            try:
                from connectors import aleads

                if aleads.configured():
                    results["aleads"] = aleads.enrich_contact(
                        name=query.name,
                        company=query.company,
                        domain=query.domain,
                        linkedin_url=query.linkedin_url,
                    )
                    al = results["aleads"]
                    sug = al.get("linkedin_url") if isinstance(al, dict) else None
                    if sug and not query.linkedin_url:
                        al["suggested_linkedin_url"] = sug
            except Exception:
                results["aleads"] = {"status": "error", "error": "contact enrichment unavailable"}

        if query.linkedin_url and "enrichlayer" not in skip:
            try:
                from connectors import enrichlayer

                if enrichlayer.configured():
                    results["enrichlayer"] = enrichlayer.fetch_profile(query.linkedin_url, timeout=15)
            except Exception:
                results["enrichlayer"] = {"status": "error", "error": "profile enrichment unavailable"}
        return results


class WebOrchestrator:
    def run(self, query: PersonQuery, skip: FrozenSet[str]) -> dict:
        results: dict = {}
        sc = query.search_constraints or None
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {}
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
        return results


class PersonalOrchestrator:
    def run(self, query: PersonQuery, skip: FrozenSet[str]) -> dict:
        if "personal_info" in skip:
            return {}
        return {
            "personal_info": personal_info.search_personal_info(
                name=query.name,
                company=query.company,
                university=query.university,
                place=query.place,
                linkedin_url=query.linkedin_url,
            )
        }


class NimbleOrchestrator:
    """Page extracts for university/company bios. Phase A = query-driven; Phase B = seed URLs."""

    def run_phase_a(self, query: PersonQuery, skip: FrozenSet[str], seed_sources: Optional[dict] = None) -> dict:
        if "nimble_pages" in skip:
            return {}
        try:
            from connectors import nimble

            if not nimble.configured():
                return {
                    "nimble_pages": {
                        "status": "skipped",
                        "reason": "page extract not configured",
                        "pages": [],
                    }
                }
            apollo_result = (seed_sources or {}).get("apollo") or {}
            gemini_result = (seed_sources or {}).get("gemini_search") or {}
            co = query.company or apollo_result.get("organization_name") or gemini_result.get("current_company")
            return {
                "nimble_pages": nimble.enrich_person_pages(
                    name=query.name,
                    company=co,
                    university=query.university,
                    linkedin_url=query.linkedin_url,
                    seed_sources=seed_sources or {},
                )
            }
        except Exception:
            return {"nimble_pages": {"status": "error", "error": "page extract unavailable", "pages": []}}


class DeepOrchestrator:
    def run(self, query: PersonQuery, skip: FrozenSet[str], results: dict, linkedin_url: Optional[str]) -> dict:
        if "deep_agent" in skip:
            return {}
        try:
            from deep_agent import run_deep_agent

            deep = run_deep_agent(
                name=query.name,
                company=query.company,
                university=query.university,
                place=query.place,
                linkedin_url=linkedin_url or query.linkedin_url,
                seed_sources=results,
            )
            return {"deep_agent": deep}
        except Exception:
            return {"deep_agent": {"status": "error", "error": "deep research unavailable"}}


class SocialOrchestrator:
    def run(
        self,
        query: PersonQuery,
        skip: FrozenSet[str],
        social_links: dict,
        identity_hints: dict,
    ) -> dict:
        results: dict = {}
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
                    search_constraints=query.search_constraints,
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
                    twitter_url=social_links.get("twitter") or social_links.get("x"),
                    identity_hints=identity_hints,
                )
            for source, future in futures.items():
                results[source] = future.result()
        return results


class SearchOrchestrator:
    """Coordinates parallel task orchestrators behind an identity gate."""

    def run(
        self,
        query: PersonQuery,
        skip: FrozenSet[str] = frozenset(),
        on_progress: ProgressCb = None,
    ) -> dict:
        results: dict = {}
        if query.linkedin_url:
            query.linkedin_locked = True

        # ── Identity (sequential) ──────────────────────────────────────────
        _emit(on_progress, "identity", 0.08, "Enriching contact and profile data…")
        results.update(IdentityOrchestrator().run(query, skip))
        _emit(on_progress, "identity", 0.16, "Identity enrichment done")

        # ── Parallel: web + personal + page extracts (phase A) ─────────────
        # When LinkedIn is already locked (Find Me), also fetch public LI in parallel.
        _emit(on_progress, "web", 0.22, "Searching public web and personal details…")
        li_known = bool(query.linkedin_url)
        with ThreadPoolExecutor(max_workers=4) as pool:
            fut_web = pool.submit(WebOrchestrator().run, query, skip)
            fut_personal = pool.submit(PersonalOrchestrator().run, query, skip)
            fut_nimble = pool.submit(
                NimbleOrchestrator().run_phase_a,
                query,
                skip,
                {"apollo": results.get("apollo")},
            )
            fut_li = None
            if li_known and "linkedin_public" not in skip:
                fut_li = pool.submit(linkedin_public.fetch_linkedin_public, query.linkedin_url)
            web = fut_web.result()
            personal = fut_personal.result()
            nimble_a = fut_nimble.result()
            if fut_li is not None:
                results["linkedin_public"] = fut_li.result()
        results.update(web)
        results.update(personal)

        gemini_result = results.get("gemini_search")
        exa_result = results.get("exa_search")
        apollo_result = results.get("apollo") or {}
        if gemini_result is None and exa_result is None and apollo_result.get("status") != "ok":
            _emit(on_progress, "web", 0.45, "Web search finished (limited hits)")
            # Still attach personal/nimble if present
            results.update(nimble_a)
            return results

        _emit(on_progress, "web", 0.45, "Web & personal research gathered")

        # Re-run page extracts with full web seeds (phase B merge)
        _emit(on_progress, "pages", 0.50, "Reading public university and company pages…")
        nimble_b = NimbleOrchestrator().run_phase_a(query, skip, seed_sources=results)
        # Prefer richer phase B pages
        results["nimble_pages"] = (nimble_b or nimble_a).get("nimble_pages") or (nimble_a or {}).get(
            "nimble_pages"
        )

        social_links = dict((gemini_result or {}).get("social_profile_links") or {})
        # Only user lock or Exa/Gemini-resolved LI becomes canonical — never Apollo/A-Leads alone
        linkedin_url = (
            query.linkedin_url
            or (exa_result or {}).get("linkedin_url")
            or social_links.get("linkedin")
        )
        if linkedin_url and not query.linkedin_url:
            query.linkedin_url = linkedin_url
            query.linkedin_locked = True

        # LinkedIn public when not already fetched in parallel with web
        if "linkedin_public" not in skip and "linkedin_public" not in results:
            results["linkedin_public"] = linkedin_public.fetch_linkedin_public(linkedin_url)

        _emit(on_progress, "deep", 0.55, "Running deeper public research…")
        results.update(DeepOrchestrator().run(query, skip, results, linkedin_url))
        deep = results.get("deep_agent") or {}
        for k, v in (deep.get("social_profile_links") or {}).items():
            if v and not social_links.get(k):
                social_links[k] = v
        _emit(on_progress, "deep", 0.68, "Deep research complete")

        linkedin_result = results.get("linkedin_public") or {}
        el_result = results.get("enrichlayer") if isinstance(results.get("enrichlayer"), dict) else {}
        photo_url = (
            apollo_result.get("photo_url")
            or el_result.get("profile_pic_url")
            or el_result.get("photo_url")
            or (el_result.get("profile") or {}).get("profile_pic_url")
            or linkedin_result.get("photo_url")
            or linkedin_result.get("profile_pic_url")
            or (gemini_result or {}).get("photo_url")
        )
        if not photo_url:
            for p in (results.get("nimble_pages") or {}).get("pages") or []:
                if not isinstance(p, dict):
                    continue
                u = p.get("final_url") or p.get("url")
                if not u:
                    continue
                try:
                    from connectors.opengraph import fetch_open_graph

                    og = fetch_open_graph(u)
                    if og.get("status") == "ok" and og.get("image"):
                        photo_url = og["image"]
                        break
                except Exception:
                    pass

        identity_hints = {
            "current_role": apollo_result.get("title") or (gemini_result or {}).get("current_role"),
            "bio_summary": (gemini_result or {}).get("bio_summary"),
            "linkedin_headline": linkedin_result.get("headline") or apollo_result.get("headline"),
            "linkedin_about": linkedin_result.get("about"),
            "photo_url": photo_url,
            "linkedin_photo_url": photo_url,
        }

        _emit(on_progress, "socials", 0.72, "Matching public social profiles…")
        results.update(SocialOrchestrator().run(query, skip, social_links, identity_hints))
        _emit(on_progress, "socials", 0.84, "Social discovery finished")
        return results
