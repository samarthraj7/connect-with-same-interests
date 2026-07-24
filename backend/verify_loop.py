"""Build a knowledge graph from research sources and verify claims before synthesize.

Rules:
- Unique identity locked to Find Me LinkedIn / chosen person — never mix same-name people.
- Re-verify against sources before merging.
- Conflicts with verified exclusive predicates are flagged, never auto-merged.
- Family/age require source URLs; age is estimate band only.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional
from urllib.parse import urlparse

from identity_lock import linkedin_slug, normalize_linkedin_url
from knowledge_graph import (
    add_edge,
    add_node,
    claims_for_llm,
    empty_graph,
    org_node_id,
    person_node_id,
    place_node_id,
    upsert_claim,
    verified_claims,
)

IDENTITY_RULE = (
    "Do not mix up same-name people. Re-verify every fact against the locked LinkedIn / "
    "chosen identity before merging. Create and keep one unique identity for the person "
    "chosen at the beginning of research."
)

HIGH_TRUST_HOST_HINTS = (
    "linkedin.com",
    "github.com",
    ".edu",
    ".ac.",
    "wikipedia.org",
)


def ingest_source(
    graph: dict,
    source_name: str,
    block: Any,
    *,
    query: Optional[dict] = None,
) -> None:
    """Ingest one connector blob into a shared knowledge graph (task-wise merge)."""
    if not isinstance(graph, dict) or not isinstance(block, dict):
        return
    query = query or {}
    identity_id = graph.get("identity_id") or linkedin_slug(
        normalize_linkedin_url(query.get("linkedin_url"))
    ) or _slug_name(query.get("name"))
    person = person_node_id(identity_id)
    add_node(
        graph,
        node_id=person,
        node_type="Person",
        name=query.get("name"),
        identity_id=identity_id,
    )
    canonical = normalize_linkedin_url(query.get("linkedin_url"))
    name = source_name
    if name == "apollo":
        _ingest_apollo(graph, block, identity_id, person)
    elif name == "linkedin_public":
        _ingest_linkedin_public(graph, block, identity_id, person, canonical)
    elif name == "gemini_search":
        _ingest_gemini(graph, block, identity_id, person, canonical)
    elif name == "personal_info":
        _ingest_personal(graph, block, identity_id, person)
    elif name == "deep_agent":
        _ingest_deep(graph, block, identity_id, person)
    elif name == "github":
        _ingest_github(graph, block, identity_id, person)
    elif name == "instagram_public":
        _ingest_social(graph, block, "instagram_handle", identity_id)
    elif name == "twitter_public":
        _ingest_social(graph, block, "twitter_handle", identity_id)
    elif name == "facebook_public":
        _ingest_social(graph, block, "facebook_handle", identity_id)
    elif name in ("nimble_pages", "page_extracts"):
        _ingest_nimble_pages(graph, block, identity_id, person)


def finalize_graph(graph: dict) -> dict:
    """Corroborate + reject unsourced family after all parallel tasks finished."""
    _corroborate(graph)
    _reject_unsourced_family(graph)
    return {
        "status": "ok",
        "identity_rule": IDENTITY_RULE,
        "knowledge_graph": graph,
        "conflicts": list(graph.get("conflicts") or []),
        "verified_count": len(verified_claims(graph)),
        "for_llm": claims_for_llm(graph),
    }


def run_verify_loop(
    *,
    query: dict,
    sources: dict,
    prior_graph: Optional[dict] = None,
) -> dict:
    """Ingest sources → claims → corroborate → return graph + llm payload."""
    canonical = normalize_linkedin_url(query.get("linkedin_url"))
    identity_id = linkedin_slug(canonical) or _slug_name(query.get("name")) or "unknown"
    graph = (
        prior_graph
        if isinstance(prior_graph, dict) and prior_graph.get("claims") is not None
        else empty_graph(identity_id=identity_id, name=query.get("name"))
    )
    graph["identity_id"] = identity_id
    person = person_node_id(identity_id)
    add_node(
        graph,
        node_id=person,
        node_type="Person",
        name=query.get("name"),
        identity_id=identity_id,
    )

    if canonical:
        upsert_claim(
            graph,
            predicate="linkedin_url",
            obj=canonical,
            text=f"Canonical LinkedIn {canonical}",
            source_urls=[canonical],
            confidence=1.0,
            status="verified",
            identity_id=identity_id,
        )

    _ingest_apollo(graph, sources.get("apollo"), identity_id, person)
    _ingest_linkedin_public(
        graph, sources.get("linkedin_public"), identity_id, person, canonical
    )
    _ingest_gemini(graph, sources.get("gemini_search"), identity_id, person, canonical)
    _ingest_personal(graph, sources.get("personal_info"), identity_id, person)
    _ingest_deep(graph, sources.get("deep_agent"), identity_id, person)
    _ingest_github(graph, sources.get("github"), identity_id, person)
    _ingest_social(graph, sources.get("instagram_public"), "instagram_handle", identity_id)
    _ingest_social(graph, sources.get("twitter_public"), "twitter_handle", identity_id)
    _ingest_social(graph, sources.get("facebook_public"), "facebook_handle", identity_id)
    _ingest_nimble_pages(graph, sources.get("nimble_pages"), identity_id, person)

    _corroborate(graph)
    _reject_unsourced_family(graph)

    return {
        "status": "ok",
        "identity_rule": IDENTITY_RULE,
        "knowledge_graph": graph,
        "conflicts": list(graph.get("conflicts") or []),
        "verified_count": len(verified_claims(graph)),
        "for_llm": claims_for_llm(graph),
    }


def _slug_name(name: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "unknown"


def _urls_from_sources_list(items: Any) -> List[str]:
    out = []
    if not isinstance(items, list):
        return out
    for it in items:
        if isinstance(it, dict):
            u = it.get("url") or it.get("source") or it.get("link")
            if u:
                out.append(str(u))
        elif isinstance(it, str) and it.startswith("http"):
            out.append(it)
    return out


def _is_high_trust(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(h in host for h in HIGH_TRUST_HOST_HINTS)


def _ingest_apollo(graph, block, identity_id, person):
    if not isinstance(block, dict) or block.get("status") != "ok":
        return
    title = block.get("title")
    org = block.get("organization_name") or block.get("company")
    urls = [block.get("linkedin_url")] if block.get("linkedin_url") else []
    if title:
        upsert_claim(
            graph,
            predicate="current_role",
            obj=title,
            text=f"Title: {title}",
            source_urls=urls,
            confidence=0.75,
            status="unverified",
            identity_id=identity_id,
        )
    if org:
        oid = org_node_id(str(org))
        add_node(graph, node_id=oid, node_type="Org", name=org)
        c = upsert_claim(
            graph,
            predicate="current_employer",
            obj=org,
            text=f"Employer: {org}",
            source_urls=urls,
            confidence=0.75,
            status="unverified",
            identity_id=identity_id,
        )
        add_edge(graph, edge_type="works_at", src=person, dst=oid, claim_ref=c.get("id"))


def _ingest_linkedin_public(graph, block, identity_id, person, canonical):
    if not isinstance(block, dict):
        return
    if not (block.get("headline") or block.get("about") or block.get("url")):
        return
    url = normalize_linkedin_url(block.get("url")) or canonical
    urls = [url] if url else []
    headline = block.get("headline")
    if headline:
        upsert_claim(
            graph,
            predicate="linkedin_headline",
            obj=headline,
            text=str(headline),
            source_urls=urls,
            confidence=0.85,
            status="verified" if urls else "unverified",
            identity_id=identity_id,
        )
    about = block.get("about")
    if about:
        upsert_claim(
            graph,
            predicate="linkedin_about",
            obj=str(about)[:500],
            text=str(about)[:500],
            source_urls=urls,
            confidence=0.8,
            status="verified" if urls else "unverified",
            identity_id=identity_id,
        )


def _ingest_gemini(graph, block, identity_id, person, canonical):
    if not isinstance(block, dict):
        return
    urls = _urls_from_sources_list(block.get("sources"))
    role = block.get("current_role")
    if role:
        upsert_claim(
            graph,
            predicate="current_role",
            obj=role,
            text=str(role),
            source_urls=urls[:3],
            confidence=0.55,
            identity_id=identity_id,
        )
    for item in block.get("career_history") or []:
        if not item:
            continue
        upsert_claim(
            graph,
            predicate="career_history",
            obj=str(item)[:300],
            text=str(item),
            source_urls=urls[:2],
            confidence=0.5,
            identity_id=identity_id,
        )
    for edu in block.get("education") or []:
        if not edu:
            continue
        text = edu if isinstance(edu, str) else str(edu)
        upsert_claim(
            graph,
            predicate="education",
            obj=text[:300],
            text=text,
            source_urls=urls[:2],
            confidence=0.5,
            identity_id=identity_id,
        )
        m = re.search(r"(University|College|Institute|School)[^,(]{0,60}", text, re.I)
        if m:
            school = m.group(0).strip()
            oid = org_node_id(school)
            add_node(graph, node_id=oid, node_type="Org", name=school)
            add_edge(graph, edge_type="attended", src=person, dst=oid)
    links = block.get("social_profile_links") or {}
    gem_li = links.get("linkedin") if isinstance(links, dict) else None
    if gem_li and canonical:
        from identity_lock import same_linkedin

        if not same_linkedin(gem_li, canonical):
            return


def _ingest_personal(graph, block, identity_id, person):
    if not isinstance(block, dict):
        return
    urls = _urls_from_sources_list(block.get("sources"))
    for ev in block.get("evidence") or []:
        if isinstance(ev, dict) and ev.get("source_hint") and str(ev["source_hint"]).startswith("http"):
            urls.append(ev["source_hint"])

    def _field(pred, val, conf=0.45):
        if not val:
            return
        if isinstance(val, list):
            for v in val:
                if not v:
                    continue
                obj = str(v)[:300] if not isinstance(v, dict) else str(v.get("name") or v)[:300]
                upsert_claim(
                    graph,
                    predicate=pred,
                    obj=obj,
                    text=str(v),
                    source_urls=urls[:3],
                    confidence=conf,
                    identity_id=identity_id,
                    claim_type=(
                        "family"
                        if pred in ("spouse", "child", "sibling", "family_background")
                        else pred
                    ),
                )
            return
        upsert_claim(
            graph,
            predicate=pred,
            obj=str(val)[:300],
            text=str(val),
            source_urls=urls[:3],
            confidence=conf,
            identity_id=identity_id,
        )

    _field("born_or_hometown", block.get("born_or_hometown"), 0.5)
    _field("raised_in", block.get("raised_in"), 0.5)
    _field("current_location", block.get("current_location"), 0.5)
    if block.get("current_location"):
        pid = place_node_id(str(block["current_location"]))
        add_node(graph, node_id=pid, node_type="Place", name=block["current_location"])
        add_edge(graph, edge_type="lives_in", src=person, dst=pid)
    _field("family_background", block.get("family_background"), 0.4)
    _field("spouse", block.get("spouse"), 0.4)
    for child in block.get("children") or []:
        if isinstance(child, dict):
            name = child.get("name")
            if not name:
                continue
            c = upsert_claim(
                graph,
                predicate="child",
                obj=name,
                text=str(child),
                source_urls=urls[:3],
                confidence=0.4,
                identity_id=identity_id,
                claim_type="family",
            )
            rid = f"person:rel-{_slug_name(name)}"
            add_node(graph, node_id=rid, node_type="Person", name=name, relation="child")
            add_edge(graph, edge_type="parent_of", src=person, dst=rid, claim_ref=c.get("id"))
            if child.get("school"):
                upsert_claim(
                    graph,
                    predicate="child_school",
                    obj=f"{name}:{child['school']}",
                    text=f"{name} school: {child['school']}",
                    source_urls=urls[:3],
                    confidence=0.35,
                    identity_id=identity_id,
                    claim_type="family",
                )
        else:
            _field("child", child, 0.4)
    for sib in block.get("siblings") or []:
        if isinstance(sib, dict) and sib.get("name"):
            upsert_claim(
                graph,
                predicate="sibling",
                obj=sib["name"],
                text=str(sib),
                source_urls=urls[:3],
                confidence=0.4,
                identity_id=identity_id,
                claim_type="family",
            )
        else:
            _field("sibling", sib, 0.4)
    if block.get("estimated_age_band"):
        upsert_claim(
            graph,
            predicate="estimated_age_band",
            obj=block["estimated_age_band"],
            text=(
                f"{block['estimated_age_band']} "
                f"({block.get('estimated_age_basis') or 'education timeline'})"
            ),
            source_urls=urls[:3],
            confidence=0.45,
            identity_id=identity_id,
        )
    _field("hobby", block.get("hobbies"), 0.4)


def _ingest_deep(graph, block, identity_id, person):
    if not isinstance(block, dict):
        return
    for ev in block.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        fact = ev.get("fact") or ev.get("text")
        url = ev.get("source_url") or ev.get("url")
        if not fact:
            continue
        cat = (ev.get("category") or "other").lower()
        pred = {
            "career": "career_history",
            "education": "education",
            "personal": "personal_note",
            "family": "family_background",
            "award": "award",
            "writing": "publication",
        }.get(cat, "fact")
        upsert_claim(
            graph,
            predicate=pred,
            obj=str(fact)[:300],
            text=str(fact),
            source_urls=[url] if url else [],
            confidence=0.55 if url else 0.25,
            identity_id=identity_id,
        )


def _ingest_github(graph, block, identity_id, person):
    if not isinstance(block, dict) or block.get("status") != "ok":
        return
    handle = block.get("username") or (block.get("profile") or {}).get("login")
    url = block.get("profile_url") or (f"https://github.com/{handle}" if handle else None)
    if handle:
        tier = (block.get("identity_match") or {}).get("tier")
        upsert_claim(
            graph,
            predicate="github_handle",
            obj=handle,
            text=f"GitHub @{handle}",
            source_urls=[url] if url else [],
            confidence=0.7 if tier == "confirmed" else 0.4,
            status="verified" if tier == "confirmed" else "unverified",
            identity_id=identity_id,
        )
    # GraphQL enrichments (orgs / linked socials / activity)
    for org in (block.get("organizations") or [])[:8]:
        if not org:
            continue
        upsert_claim(
            graph,
            predicate="github_org",
            obj=str(org),
            text=f"GitHub org: {org}",
            source_urls=[url] if url else [],
            confidence=0.55,
            status="unverified",
            identity_id=identity_id,
        )
    for acct in (block.get("social_accounts") or [])[:8]:
        if not isinstance(acct, dict):
            continue
        provider = (acct.get("provider") or "").lower()
        handle_s = acct.get("handle") or acct.get("displayName")
        if not provider or not handle_s:
            continue
        upsert_claim(
            graph,
            predicate=f"github_linked_{provider}",
            obj=str(handle_s),
            text=f"GitHub-linked {provider}: {handle_s}",
            source_urls=[acct.get("url") or url] if (acct.get("url") or url) else [],
            confidence=0.6,
            status="unverified",
            identity_id=identity_id,
        )
    contrib = block.get("contributions") or {}
    total = contrib.get("total") or contrib.get("total_last_year")
    if total is not None:
        upsert_claim(
            graph,
            predicate="github_contributions_year",
            obj=str(total),
            text=f"~{total} GitHub contributions last year",
            source_urls=[url] if url else [],
            confidence=0.5,
            status="unverified",
            identity_id=identity_id,
        )


def _ingest_social(graph, block, predicate, identity_id):
    if not isinstance(block, dict) or block.get("status") != "ok":
        return
    conf = (block.get("match_confidence") or "").lower()
    if conf not in ("high", "medium"):
        return
    handle = block.get("handle")
    url = block.get("profile_url")
    if not handle:
        return
    upsert_claim(
        graph,
        predicate=predicate,
        obj=handle,
        text=f"{predicate} @{handle}",
        source_urls=[url] if url else [],
        confidence=0.85 if conf == "high" else 0.65,
        status="verified" if conf == "high" else "unverified",
        identity_id=identity_id,
    )


def _ingest_nimble_pages(graph, block, identity_id, person):
    if not isinstance(block, dict):
        return
    for page in block.get("pages") or []:
        if not isinstance(page, dict):
            continue
        url = page.get("final_url") or page.get("url")
        text = (page.get("markdown") or page.get("text") or "")[:2000]
        if not url or not text:
            continue
        title = page.get("title") or url
        upsert_claim(
            graph,
            predicate="page_extract",
            obj=str(title)[:200],
            text=text[:400],
            source_urls=[url],
            confidence=0.6,
            identity_id=identity_id,
        )


def _corroborate(graph: dict) -> None:
    """Promote claims with enough independent sources or one high-trust URL."""
    for claim in graph.get("claims") or []:
        if claim.get("status") in ("conflict", "rejected", "verified"):
            continue
        urls = list(claim.get("source_urls") or [])
        hosts = set()
        for u in urls:
            host = (urlparse(u).netloc or "").lower()
            if host:
                hosts.add(host)
        high = any(_is_high_trust(u) for u in urls)
        if high and urls:
            claim["status"] = "verified"
            claim["confidence"] = max(float(claim.get("confidence") or 0), 0.8)
        elif len(hosts) >= 2:
            claim["status"] = "verified"
            claim["confidence"] = max(float(claim.get("confidence") or 0), 0.7)
        elif len(urls) >= 2 and float(claim.get("confidence") or 0) >= 0.55:
            claim["status"] = "verified"


def _reject_unsourced_family(graph: dict) -> None:
    family_preds = {
        "spouse",
        "child",
        "sibling",
        "family_background",
        "child_school",
        "estimated_age_band",
    }
    for claim in graph.get("claims") or []:
        if claim.get("predicate") not in family_preds and claim.get("type") != "family":
            continue
        if not (claim.get("source_urls") or []):
            claim["status"] = "rejected"
            claim["reject_reason"] = "family/age claim requires a public source URL"
