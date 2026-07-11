import time

from google.genai import errors

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


def generate_with_retry(client, **kwargs):
    """client.models.generate_content with a short backoff-and-retry on 5xx —
    Gemini's own error message calls a 503 overload "usually temporary", so a
    few retries is real resilience, not an attempt to force through a block."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.models.generate_content(**kwargs)
        except errors.ServerError as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise last_error
