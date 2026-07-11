"""Simple password hashing + JWT for the mobile API."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any, Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.users import UserStore

_bearer = HTTPBearer(auto_error=False)
_store = UserStore()

JWT_SECRET = os.environ.get("API_JWT_SECRET") or "connect-deeply-dev-secret-change-me"
TOKEN_TTL_SEC = int(os.environ.get("API_TOKEN_TTL_SEC", str(60 * 60 * 24 * 30)))


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return hmac.compare_digest(check, digest)


def create_token(user_id: str, email: str) -> str:
    """Lightweight signed token (not full JWT lib — fine for MVP)."""
    exp = int(time.time()) + TOKEN_TTL_SEC
    payload = f"{user_id}|{email}|{exp}"
    sig = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def decode_token(token: str) -> dict[str, Any]:
    try:
        user_id, email, exp_s, sig = token.split("|", 3)
        exp = int(exp_s)
    except ValueError as exc:
        raise HTTPException(401, "Invalid token") from exc
    payload = f"{user_id}|{email}|{exp}"
    expected = hmac.new(JWT_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, "Invalid token")
    if time.time() > exp:
        raise HTTPException(401, "Token expired")
    return {"id": user_id, "email": email}


def require_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    if not creds or not creds.credentials:
        raise HTTPException(401, "Not authenticated")
    claims = decode_token(creds.credentials)
    user = _store.get(claims["id"])
    if not user:
        raise HTTPException(401, "User not found")
    return user
