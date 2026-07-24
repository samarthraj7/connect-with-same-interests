from datetime import datetime, timezone


def merge_profile(query: dict, raw_results: dict) -> dict:
    """Combine every connector's raw output into a single profile document —
    this dict is what gets written to disk and handed to Claude."""
    source_status = {
        name: result.get("status", "unknown")
        for name, result in raw_results.items()
        if not name.startswith("_")
    }

    return {
        "query": query,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_status": source_status,
        "sources": raw_results,
    }


def attach_verified_knowledge_graph(merged: dict, *, prior_graph: dict = None) -> dict:
    """Run verify_loop and attach reconciled claims for synthesize + persistence.

    If orchestrator already built a shared graph (`sources._knowledge_graph`) with
    claims, finalize it (skip full re-ingestion). Otherwise fall back to
    run_verify_loop with prior_graph / empty seed.
    """
    from verify_loop import finalize_graph, run_verify_loop

    sources = merged.get("sources") or {}
    live = sources.pop("_knowledge_graph", None) if isinstance(sources, dict) else None
    if isinstance(live, dict) and live.get("claims"):
        result = finalize_graph(live)
    else:
        seed = prior_graph if isinstance(prior_graph, dict) else None
        result = run_verify_loop(
            query=merged.get("query") or {},
            sources=sources,
            prior_graph=seed,
        )
    merged["knowledge_graph"] = result.get("knowledge_graph")
    merged["conflicts"] = result.get("conflicts") or []
    merged["knowledge_graph_for_llm"] = result.get("for_llm") or {}
    merged["verify_status"] = {
        "status": result.get("status"),
        "verified_count": result.get("verified_count"),
        "identity_rule": result.get("identity_rule"),
    }
    return merged
