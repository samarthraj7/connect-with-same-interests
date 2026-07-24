"""Generate plausible social/GitHub username variants from a real name.

Examples for "Samarth Rajendra":
  samarthrajendra, samarth_rajendra, samarth.rajendra, srajendra,
  sam_raj, samrajendra, samarth_raj, s_rajendra, …
"""

from __future__ import annotations

import re
from typing import List, Optional


def username_variants(name: str, *, linkedin_slug: Optional[str] = None, limit: int = 12) -> List[str]:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", (name or "").strip()) if p]
    if not parts and not linkedin_slug:
        return []
    guesses: List[str] = []
    if linkedin_slug:
        slug = re.sub(r"[^a-z0-9._-]", "", linkedin_slug.lower())
        if slug:
            guesses.append(slug)
            guesses.append(slug.replace("-", "_"))
            guesses.append(slug.replace("-", ""))

    if not parts:
        return _dedupe(guesses, limit)

    first = parts[0].lower()
    last = parts[-1].lower() if len(parts) > 1 else ""
    mid = parts[1].lower() if len(parts) > 2 else ""

    if last:
        guesses.extend(
            [
                f"{first}{last}",
                f"{first}_{last}",
                f"{first}.{last}",
                f"{first}-{last}",
                f"{first[0]}{last}" if first else last,
                f"{first}{last[0]}" if last else first,
                f"{first[:3]}_{last}" if len(first) >= 3 else f"{first}_{last}",
                f"{first[:3]}{last}" if len(first) >= 3 else f"{first}{last}",
                f"{first}_{last[:3]}" if len(last) >= 3 else f"{first}_{last}",
                # compressed: sam_rthraj style — first short + letters from last
                f"{first[:3]}_{last[0]}{last[1:4]}" if len(first) >= 3 and len(last) >= 2 else "",
                f"{first[0]}_{last}",
                f"{last}{first}",
                f"{last}_{first}",
            ]
        )
        if mid:
            guesses.extend(
                [
                    f"{first}_{mid[0]}{last}",
                    f"{first[0]}{mid[0]}{last}",
                    f"{first}{mid[0]}{last}",
                ]
            )
    else:
        guesses.append(first)

    return _dedupe(guesses, limit)


def _dedupe(guesses: List[str], limit: int) -> List[str]:
    out: List[str] = []
    seen = set()
    for g in guesses:
        g = re.sub(r"[^a-z0-9._-]", "", (g or "").lower())[:39]
        if len(g) < 3 or g in seen:
            continue
        seen.add(g)
        out.append(g)
        if len(out) >= limit:
            break
    return out
