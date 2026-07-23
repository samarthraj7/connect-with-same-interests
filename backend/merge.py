from datetime import datetime, timezone


def merge_profile(query: dict, raw_results: dict) -> dict:
    """Combine every connector's raw output into a single profile document —
    this dict is what gets written to disk and handed to Claude."""
    source_status = {name: result.get("status", "unknown") for name, result in raw_results.items()}

    return {
        "query": query,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_status": source_status,
        "sources": raw_results,
    }


def attach_verified_knowledge_graph(merged: dict, *, prior_graph: dict = None) -> dict:
    """Run verify_loop and attach reconciled claims for synthesize + persistence."""
    from verify_loop import run_verify_loop

    result = run_verify_loop(
        query=merged.get("query") or {},
        sources=merged.get("sources") or {},
        prior_graph=prior_graph,
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
