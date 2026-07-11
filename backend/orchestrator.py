from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import FrozenSet, Optional

from connectors import (
    exa_search,
    facebook,
    gemini_search,
    github,
    instagram,
    linkedin_public,
    patents,
    personal_info,
    twitter,
)


@dataclass
class PersonQuery:
    name: str
    company: Optional[str] = None
    university: Optional[str] = None
    place: Optional[str] = None
    github_username: Optional[str] = None
    linkedin_url: Optional[str] = None


class SearchOrchestrator:
    """Fans out to every connector not in `skip`, in two waves.

    Wave 1: github, patents, gemini_search, exa_search, personal_info.
    Wave 2a: linkedin_public (needs discovered LinkedIn URL).
    Wave 2b: Instagram / Facebook / Twitter — opt-in only (pass them in
    ``skip`` to leave them off). Each does Google + ScrapeCreators when run.
    """

    def run(self, query: PersonQuery, skip: FrozenSet[str] = frozenset()) -> dict:
        results = {}

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {}
            if "github" not in skip:
                futures["github"] = pool.submit(
                    github.search_github, name=query.name, username=query.github_username, company=query.company
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
                )
            if "exa_search" not in skip:
                futures["exa_search"] = pool.submit(
                    exa_search.search_person_exa,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                )
            if "personal_info" not in skip:
                futures["personal_info"] = pool.submit(
                    personal_info.search_personal_info,
                    name=query.name,
                    company=query.company,
                    university=query.university,
                    place=query.place,
                )
            for source, future in futures.items():
                results[source] = future.result()

        gemini_result = results.get("gemini_search")
        exa_result = results.get("exa_search")
        if gemini_result is None and exa_result is None:
            return results

        social_links = (gemini_result or {}).get("social_profile_links") or {}
        linkedin_url = query.linkedin_url or (exa_result or {}).get("linkedin_url") or social_links.get("linkedin")

        if "linkedin_public" not in skip:
            results["linkedin_public"] = linkedin_public.fetch_linkedin_public(linkedin_url)

        linkedin_result = results.get("linkedin_public") or {}
        identity_hints = {
            "current_role": (gemini_result or {}).get("current_role"),
            "bio_summary": (gemini_result or {}).get("bio_summary"),
            "linkedin_headline": linkedin_result.get("headline"),
            "linkedin_about": linkedin_result.get("about"),
        }

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
                    twitter_url=social_links.get("twitter") or social_links.get("x"),
                    identity_hints=identity_hints,
                )
            for source, future in futures.items():
                results[source] = future.result()

        return results
