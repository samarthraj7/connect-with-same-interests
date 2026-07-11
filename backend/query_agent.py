import json
import os
from typing import Callable, List, Optional

from google import genai
from google.genai import errors, types

from gemini_retry import generate_with_retry

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
MAX_ATTEMPTS = 4

PLANNER_PROMPT = """You are a search-query planning agent. Propose ONE search query that best \
makes progress toward this goal.

GOAL: {goal}

CONTEXT already known about the target:
{context}

QUERIES ALREADY TRIED THIS SESSION (all failed to meet the goal):
{history}

Propose the next query. It must be meaningfully different from every query already tried —
pick a different strategy, don't just reword the same one. Strategies to draw on:
- Swap which context you lead with: company, then university/school, then location/city, then
  a role/title if one was discovered by an earlier attempt.
- Target the exact URL pattern directly (e.g. lead with "linkedin.com/in" or
  "instagram.com/" / "site:instagram.com" in the query text depending on the goal).
- Try phrasing that indexed page snippets commonly contain (e.g. "connect with", "view profile",
  "Linktree", "follow me on Instagram").
- Try a name variant: full legal name vs. common nickname vs. initials.
- If a previous result kept surfacing a specific wrong/unrelated match, exclude it explicitly
  (e.g. "-@wronghandle").
- Pivot to an adjacent source that might link to what you want (e.g. search for a bio page,
  press mention, personal website, or another platform's profile that often cross-links to it).
- If everything so far was too narrow, drop a constraint; if too broad, add a distinguishing detail.

Respond with strict JSON only, no markdown fences:
{{"query": string, "include_domains": [string] or null, "reasoning": string}}
"""


def run_goal_directed_search(
    goal: str,
    context: dict,
    execute_query: Callable[[str, Optional[List[str]]], list],
    check_success: Callable[[list], bool],
    initial_query: str,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict:
    """Goal-directed query loop: run `initial_query` first (the obvious
    baseline, no need to burn an LLM call proposing it), check success, and
    if it fails, ask Gemini to propose a genuinely different next query
    given the full history of what's already failed — rather than running
    one fixed query and accepting whatever it returns.

    `execute_query(query, include_domains) -> list[result]` runs the search.
    `check_success(results) -> bool` decides whether the goal was met.
    Returns {"status", "result", "attempts"} — `attempts` is the full
    history for transparency/debugging, printed as it goes."""
    history: list = []
    result: list = []
    query = initial_query
    include_domains: Optional[List[str]] = None
    reasoning = "baseline query"

    for attempt in range(1, max_attempts + 1):
        domain_note = f" (domains={include_domains})" if include_domains else ""
        print(f"  [query agent] attempt {attempt}: {query!r}{domain_note} — {reasoning}")

        result = execute_query(query, include_domains)
        success = check_success(result)
        history.append({"attempt": attempt, "query": query, "include_domains": include_domains, "reasoning": reasoning, "success": success})

        if success:
            print(f"  [query agent] goal met on attempt {attempt}")
            return {"status": "ok", "result": result, "attempts": history}

        if attempt < max_attempts:
            plan = _plan_next_query(goal, context, history)
            if plan is None:
                break  # no Gemini key / planning failed — stop rather than repeat the same query
            query, include_domains, reasoning = plan["query"], plan.get("include_domains"), plan.get("reasoning", "")

    print(f"  [query agent] goal not met after {len(history)} attempt(s)")
    return {"status": "not_found", "result": result, "attempts": history}


def _plan_next_query(goal: str, context: dict, history: list) -> Optional[dict]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None

    history_text = "\n".join(
        f"- {h['query']!r} (domains={h['include_domains']}) — did not meet the goal" for h in history
    ) or "(none yet)"
    prompt = PLANNER_PROMPT.format(goal=goal, context=json.dumps(context, indent=2), history=history_text)

    try:
        client = genai.Client(api_key=api_key)
        response = generate_with_retry(
            client,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return json.loads(response.text or "")
    except (errors.ClientError, errors.ServerError, json.JSONDecodeError):
        return None
