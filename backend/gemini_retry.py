import time

from google.genai import errors

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2


def generate_with_retry(client, **kwargs):
    """client.models.generate_content with a short backoff-and-retry on 5xx —
    Gemini's own error message calls a 503 overload "usually temporary", so a
    few retries is real resilience, not an attempt to force through a block."""
    print(
        f"[gemini_retry] generate model={kwargs.get('model')!r}",
        flush=True,
    )
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            print(f"[gemini_retry] attempt {attempt + 1}/{MAX_RETRIES}…", flush=True)
            result = client.models.generate_content(**kwargs)
            print(f"[gemini_retry] attempt {attempt + 1} OK", flush=True)
            return result
        except errors.ServerError as exc:
            last_error = exc
            print(f"[gemini_retry] attempt {attempt + 1} ServerError: {exc}", flush=True)
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_SECONDS * (attempt + 1)
                print(f"[gemini_retry] sleeping {delay}s then retry…", flush=True)
                time.sleep(delay)
        except Exception as exc:
            print(f"[gemini_retry] attempt {attempt + 1} {type(exc).__name__}: {exc}", flush=True)
            raise
    print(f"[gemini_retry] giving up after {MAX_RETRIES} attempts", flush=True)
    raise last_error
