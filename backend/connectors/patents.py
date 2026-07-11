import json
import os

import requests

PATENTSVIEW_API = "https://search.patentsview.org/api/v1/patent/"


def search_patents(name: str) -> dict:
    """Look up patents by inventor name via the PatentsView API.
    Requires a free API key: https://search.patentsview.org/api/keyrequest/
    Skips cleanly (does not fail the whole search) if no key is configured."""
    api_key = os.environ.get("PATENTSVIEW_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "PATENTSVIEW_API_KEY not set"}

    parts = name.strip().split()
    if len(parts) < 2:
        return {"status": "skipped", "reason": "need a first and last name for patent search"}
    first_name, last_name = parts[0], parts[-1]

    query = {
        "_and": [
            {"inventors.inventor_name_first": first_name},
            {"inventors.inventor_name_last": last_name},
        ]
    }
    fields = ["patent_id", "patent_title", "patent_date"]

    try:
        resp = requests.get(
            PATENTSVIEW_API,
            headers={"X-Api-Key": api_key},
            params={"q": json.dumps(query), "f": json.dumps(fields)},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        patents = data.get("patents") or []
        if not patents:
            return {"status": "not_found", "patents": []}
        return {
            "status": "ok",
            "patents": [
                {"title": p.get("patent_title"), "id": p.get("patent_id"), "date": p.get("patent_date")}
                for p in patents[:5]
            ],
        }
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)}
    except (ValueError, KeyError) as exc:
        return {"status": "error", "error": f"unexpected response shape: {exc}"}
