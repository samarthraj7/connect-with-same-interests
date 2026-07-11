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
