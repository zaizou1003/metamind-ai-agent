import time
import random
from mistralai.models import SDKError

def call_with_retry(
    fn,
    *,
    max_retries: int = 5,
    base_delay: float = 0.8,
    max_delay: float = 8.0,
):
    """
    Generic retry wrapper for Mistral calls.
    Retries on 429 / rate-limit errors with exponential backoff.
    """
    attempt = 0

    while True:
        try:
            return fn()

        except SDKError as e:
            msg = str(e).lower()

            # retry ONLY on rate limit / capacity
            if "429" in msg or "rate" in msg or "capacity" in msg:
                attempt += 1
                if attempt > max_retries:
                    raise

                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                jitter = random.uniform(0, delay * 0.3)
                sleep_time = delay + jitter

                print(f"[LLM retry] attempt {attempt}/{max_retries} – sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
                continue

            # other errors → fail fast
            raise
