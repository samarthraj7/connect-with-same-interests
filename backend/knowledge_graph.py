"""Claim-level knowledge graph for person research.

Nodes and edges are plain dicts persisted as JSON on drafts/profiles.
Every claim keeps source URLs, confidence, and status for verify-before-summarize.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, List, Optional


CLAIM_STATUSES = ("verified", "unverified", "conflict", "rejected")

# Predicates that should not auto-merge when values disagree
EXCLUSIVE_PREDICATES = frozenset(
    {
        "current_role",
        "current_employer",
        "spouse",
        "born_or_hometown",
        "estimated_age_band",
        "linkedin_url",
        "github_handle",
        "instagram_handle",
    }
)


def _slug(s: Optional[str]) -> str:
    t = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return t or "unknown"


def claim_id(predicate: str, obj: Any, identity_id: str) -> str:
    raw = f"{identity_id}|{predicate}|{obj}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def empty_graph(*, identity_id: str, name: Optional[str] = None) -> dict:
    person_id = f"person:{identity_id}"
    return {
        "identity_id": identity_id,
        "nodes": {
            person_id: {
                "id": person_id,
                "type": "Person",
                "name": name,
                "identity_id": identity_id,
            }
        },
        "edges": [],
        "claims": [],
        "conflicts": [],
    }


def add_node(graph: dict, *, node_id: str, node_type: str, **attrs: Any) -> str:
    nodes = graph.setdefault("nodes", {})
    if node_id not in nodes:
        nodes[node_id] = {"id": node_id, "type": node_type, **attrs}
    else:
        nodes[node_id].update({k: v for k, v in attrs.items() if v is not None})
    return node_id


def add_edge(
    graph: dict,
    *,
    edge_type: str,
    src: str,
    dst: str,
    claim_ref: Optional[str] = None,
) -> None:
    edges = graph.setdefault("edges", [])
    key = (edge_type, src, dst)
    if any((e.get("type"), e.get("src"), e.get("dst")) == key for e in edges):
        return
    edges.append({"type": edge_type, "src": src, "dst": dst, "claim_ref": claim_ref})


def upsert_claim(
    graph: dict,
    *,
    predicate: str,
    obj: Any,
    text: str,
    source_urls: Optional[Iterable[str]] = None,
    confidence: float = 0.4,
    status: str = "unverified",
    identity_id: Optional[str] = None,
    claim_type: Optional[str] = None,
) -> dict:
    """Insert or strengthen a claim. Conflicts with verified exclusive predicates are flagged."""
    identity_id = identity_id or graph.get("identity_id") or "unknown"
    urls = [u for u in (source_urls or []) if u and str(u).startswith("http")]
    cid = claim_id(predicate, obj, identity_id)
    claims: List[dict] = graph.setdefault("claims", [])
    existing = next((c for c in claims if c.get("id") == cid), None)
    if existing:
        # Merge sources / bump confidence
        seen = set(existing.get("source_urls") or [])
        for u in urls:
            if u not in seen:
                existing.setdefault("source_urls", []).append(u)
                seen.add(u)
        existing["confidence"] = max(float(existing.get("confidence") or 0), float(confidence))
        if text and len(text) > len(existing.get("text") or ""):
            existing["text"] = text
        return existing

    # Conflict detection against other claims with same exclusive predicate
    if predicate in EXCLUSIVE_PREDICATES:
        for other in claims:
            if other.get("predicate") != predicate:
                continue
            if other.get("status") == "rejected":
                continue
            if _normalize_obj(other.get("object")) == _normalize_obj(obj):
                continue
            # Disagreement
            if other.get("status") == "verified" or status == "verified":
                conflict = {
                    "predicate": predicate,
                    "existing_claim_id": other.get("id"),
                    "new_claim_id": cid,
                    "existing_object": other.get("object"),
                    "new_object": obj,
                    "existing_sources": list(other.get("source_urls") or []),
                    "new_sources": urls,
                    "status": "conflict",
                }
                graph.setdefault("conflicts", []).append(conflict)
                status = "conflict"
                if other.get("status") == "verified":
                    # Do not demote verified; mark new as conflict only
                    pass
                else:
                    other["status"] = "conflict"

    claim = {
        "id": cid,
        "type": claim_type or predicate,
        "predicate": predicate,
        "object": obj,
        "text": text,
        "source_urls": urls,
        "confidence": float(confidence),
        "status": status if status in CLAIM_STATUSES else "unverified",
        "identity_id": identity_id,
    }
    claims.append(claim)
    return claim


def _normalize_obj(obj: Any) -> str:
    return re.sub(r"\s+", " ", str(obj or "").strip().lower())


def verified_claims(graph: dict) -> List[dict]:
    return [c for c in (graph.get("claims") or []) if c.get("status") == "verified"]


def claims_for_llm(graph: dict) -> dict:
    """Compact view for synthesize: verified facts + conflict list (no auto-merge)."""
    verified = []
    unverified = []
    for c in graph.get("claims") or []:
        row = {
            "predicate": c.get("predicate"),
            "object": c.get("object"),
            "text": c.get("text"),
            "source_urls": c.get("source_urls") or [],
            "confidence": c.get("confidence"),
            "status": c.get("status"),
        }
        if c.get("status") == "verified":
            verified.append(row)
        elif c.get("status") == "unverified":
            unverified.append(row)
    return {
        "identity_id": graph.get("identity_id"),
        "verified_claims": verified,
        "unverified_claims": unverified[:40],
        "conflicts": list(graph.get("conflicts") or []),
        "node_count": len(graph.get("nodes") or {}),
        "edge_count": len(graph.get("edges") or {}),
    }


def person_node_id(identity_id: str) -> str:
    return f"person:{identity_id}"


def org_node_id(name: str) -> str:
    return f"org:{_slug(name)}"


def place_node_id(name: str) -> str:
    return f"place:{_slug(name)}"
