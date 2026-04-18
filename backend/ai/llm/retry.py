"""Exponential backoff retry wrapper for LLM API calls."""

import asyncio
import logging
import re
from functools import wraps
from typing import Type

logger = logging.getLogger(__name__)

# Retriable exception types per provider
RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class LLMRetryError(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, message: str, last_exception: Exception):
        super().__init__(message)
        self.last_exception = last_exception


def _extract_retry_delay(exception: Exception) -> float | None:
    """Try to extract a retry delay hint from the exception message."""
    msg = str(exception)
    # Match patterns like "retry in 57.18s", "retryDelay: 55s", "Please retry in 4.5s"
    match = re.search(r'retry\w*\s*(?:in|:\s*)\s*["\']?(\d+(?:\.\d+)?)\s*s', msg, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retriable_exceptions: tuple[Type[Exception], ...] = (Exception,),
):
    """Decorator that adds exponential backoff retry to async functions.

    Respects retry-after hints from API error messages when available.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        retriable_exceptions: Tuple of exception types to retry on.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retriable_exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        break

                    # Use API-suggested delay if available, otherwise exponential backoff
                    api_delay = _extract_retry_delay(e)
                    if api_delay and api_delay > 0:
                        delay = min(api_delay + 1.0, max_delay)  # Add 1s buffer
                    else:
                        delay = min(base_delay * (2 ** attempt), max_delay)

                    logger.warning(
                        f"LLM call failed (attempt {attempt + 1}/{max_retries + 1}): "
                        f"{type(e).__name__}: {str(e)[:200]}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)

            raise LLMRetryError(
                f"All {max_retries + 1} attempts failed for {func.__name__}",
                last_exception,
            )

        return wrapper

    return decorator
