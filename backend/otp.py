"""Lightweight OTP for email/phone verification (MVP).

Authenticated flow: keyed by user_id + channel.
Signup (pre-auth) flow: keyed by email; verify returns a short-lived token
that /auth/signup must present.

In production, wire SMTP / SMS; for local/dev set OTP_DEBUG=true to return
the code in the API response.
"""

from __future__ import annotations

import hashlib
import os
import random
import secrets
import time
from typing import Any, Optional

# key -> {code, expires_at, channel, destination, attempts}
_OTP_STORE: dict[str, dict[str, Any]] = {}
# email_verified_token -> {email, expires_at}
_EMAIL_TOKENS: dict[str, dict[str, Any]] = {}

TTL_SECONDS = int(os.environ.get("OTP_TTL_SECONDS", "600"))
EMAIL_TOKEN_TTL = int(os.environ.get("OTP_EMAIL_TOKEN_TTL", "1800"))


def _key(user_id: str, channel: str) -> str:
    return f"{user_id}:{channel}"


def _email_key(email: str) -> str:
    return f"signup:email:{(email or '').strip().lower()}"


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
    return _otp_response(channel, destination, code)


def verify_otp(user_id: str, channel: str, code: str) -> dict[str, Any]:
    channel = channel.lower().strip()
    return _verify_entry(_key(user_id, channel), code, channel)


def issue_signup_email_otp(email: str) -> dict[str, Any]:
    """Pre-auth: send OTP to prove ownership of signup email."""
    dest = (email or "").strip().lower()
    if not dest or "@" not in dest:
        raise ValueError("Valid email required")
    code = f"{random.randint(0, 999999):06d}"
    entry = {
        "code": code,
        "expires_at": time.time() + TTL_SECONDS,
        "channel": "email",
        "destination": dest,
        "attempts": 0,
    }
    _OTP_STORE[_email_key(dest)] = entry
    print(f"[otp] signup email code → {dest}")
    # TODO: send via SMTP/Resend when configured
    return _otp_response("email", dest, code)


def verify_signup_email_otp(email: str, code: str) -> dict[str, Any]:
    """Verify signup OTP; returns email_verified_token for /auth/signup."""
    dest = (email or "").strip().lower()
    result = _verify_entry(_email_key(dest), code, "email")
    if result.get("status") != "ok":
        return result
    token = secrets.token_urlsafe(24)
    _EMAIL_TOKENS[token] = {
        "email": dest,
        "expires_at": time.time() + EMAIL_TOKEN_TTL,
    }
    result["email_verified_token"] = token
    result["email"] = dest
    return result


def consume_email_verified_token(token: Optional[str], email: str) -> bool:
    """One-time use: True if token matches email and is unexpired."""
    if not token:
        return False
    entry = _EMAIL_TOKENS.pop(token.strip(), None)
    if not entry:
        return False
    if time.time() > float(entry["expires_at"]):
        return False
    return (entry.get("email") or "").lower() == (email or "").strip().lower()


def _verify_entry(store_key: str, code: str, channel: str) -> dict[str, Any]:
    entry = _OTP_STORE.get(store_key)
    if not entry:
        return {"status": "error", "error": "No code pending — request a new one"}
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    if entry["attempts"] > 8:
        _OTP_STORE.pop(store_key, None)
        return {"status": "error", "error": "Too many attempts — request a new code"}
    if time.time() > float(entry["expires_at"]):
        _OTP_STORE.pop(store_key, None)
        return {"status": "error", "error": "Code expired"}
    if (code or "").strip() != entry["code"]:
        return {"status": "error", "error": "Incorrect code"}
    _OTP_STORE.pop(store_key, None)
    return {"status": "ok", "channel": channel, "verified": True}


def _otp_response(channel: str, destination: str, code: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": "ok",
        "channel": channel,
        "destination_hint": _hint(destination),
        "expires_in": TTL_SECONDS,
    }
    if (os.environ.get("OTP_DEBUG") or "").lower() in ("1", "true", "yes"):
        out["debug_code"] = code
    return out


def _hint(destination: str) -> str:
    d = destination or ""
    if "@" in d:
        local, _, domain = d.partition("@")
        return f"{local[:2]}***@{domain}"
    digits = "".join(c for c in d if c.isdigit())
    if len(digits) >= 4:
        return f"***{digits[-4:]}"
    return "***"
