"""Lightweight OTP for email/phone verification (MVP).

Codes are stored in-memory (and mirrored onto the user JSON when authenticated).
In production, wire SMTP / SMS providers; for local/dev the code is returned in
the API response when OTP_DEBUG=true so you can test without a mailer.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Optional

# key -> {code, expires_at, channel, destination}
_OTP_STORE: dict[str, dict[str, Any]] = {}
TTL_SECONDS = int(os.environ.get("OTP_TTL_SECONDS", "600"))


def _key(user_id: str, channel: str) -> str:
    return f"{user_id}:{channel}"


def issue_otp(user_id: str, channel: str, destination: str) -> dict[str, Any]:
    channel = channel.lower().strip()
    if channel not in ("email", "phone"):
        raise ValueError("channel must be email or phone")
    code = f"{random.randint(0, 999999):06d}"
    entry = {
        "code": code,
        "expires_at": time.time() + TTL_SECONDS,
        "channel": channel,
        "destination": destination,
        "attempts": 0,
    }
    _OTP_STORE[_key(user_id, channel)] = entry
    print(f"[otp] issued {channel} code for user {user_id[:8]}… → {destination}")
    out: dict[str, Any] = {
        "status": "ok",
        "channel": channel,
        "destination_hint": _hint(destination),
        "expires_in": TTL_SECONDS,
    }
    if (os.environ.get("OTP_DEBUG") or "").lower() in ("1", "true", "yes"):
        out["debug_code"] = code
    return out


def verify_otp(user_id: str, channel: str, code: str) -> dict[str, Any]:
    channel = channel.lower().strip()
    entry = _OTP_STORE.get(_key(user_id, channel))
    if not entry:
        return {"status": "error", "error": "No code pending — request a new one"}
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    if entry["attempts"] > 8:
        _OTP_STORE.pop(_key(user_id, channel), None)
        return {"status": "error", "error": "Too many attempts — request a new code"}
    if time.time() > float(entry["expires_at"]):
        _OTP_STORE.pop(_key(user_id, channel), None)
        return {"status": "error", "error": "Code expired"}
    if (code or "").strip() != entry["code"]:
        return {"status": "error", "error": "Incorrect code"}
    _OTP_STORE.pop(_key(user_id, channel), None)
    return {"status": "ok", "channel": channel, "verified": True}


def _hint(destination: str) -> str:
    d = destination or ""
    if "@" in d:
        local, _, domain = d.partition("@")
        return f"{local[:2]}***@{domain}"
    digits = "".join(c for c in d if c.isdigit())
    if len(digits) >= 4:
        return f"***{digits[-4:]}"
    return "***"
