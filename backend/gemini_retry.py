"""Gemini generate_content with backoff on 5xx and rate-limit (429) ClientErrors."""

from __future__ import annotations

import time

from google.genai import errors

MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 2


def _is_retryable_client_error(exc: BaseException) -> bool:
    """429 / RESOURCE_EXHAUSTED / transient overload — retry; other 4xx — fail fast."""
    msg = str(exc).lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 503, 502, 500):
        return True
    needles = (
        "429",
        "resource_exhausted",
        "resource exhausted",
        "rate limit",
        "quota",
        "too many requests",
        "unavailable",
        "overloaded",
    )
    return any(n in msg for n in needles)


def generate_with_retry(client, **kwargs):
    """client.models.generate_content with backoff on 5xx and rate-limit ClientErrors."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.models.generate_content(**kwargs)
        except errors.ServerError as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise
        except errors.ClientError as exc:
            last_error = exc
            if _is_retryable_client_error(exc) and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (2 ** attempt))
                continue
            raise
        except Exception:
            raise
    raise last_error


def user_facing_gemini_error(_exc: BaseException | None = None) -> str:
    """Generic message for API/clients — never leak SDK/vendor details."""
    return "Research model temporarily unavailable. Please try again in a moment."
