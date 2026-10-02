"""Transient LLM failure classification and bounded retry with backoff."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable, TypeVar

_logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_context_window_error(exc: BaseException) -> bool:
    """Input overflow only; generic output/max_tokens errors are not retryable."""
    text = str(exc).lower()
    return any(marker in text for marker in (
        "context_length_exceeded", "context_window_exceeded", "maximum context length",
        "exceeds the context window", "exceeded the model context", "prompt is too long",
        "input is too long", "input tokens exceed", "too many tokens", "context length limit",
        "context window limit", "context limit exceeded",
    ))


def context_window_from_error(exc: BaseException) -> int | None:
    """Read a provider-reported maximum, never the submitted prompt token count."""
    match = re.search(
        r"(?:maximum context length|context (?:window|length)(?: limit)?|max(?:imum)? input tokens)"
        r"\s*(?:is|of|:|=)?\s*([\d,]+)\s*tokens?", str(exc), re.IGNORECASE,
    )
    if match:
        value = int(match[1].replace(",", ""))
        if 1024 <= value <= 50_000_000:
            return value
    return None

# Default: one retry after the initial attempt (2 attempts total).
_DEFAULT_ATTEMPTS = 2
_BASE_DELAY_S = 0.75


def is_transient_llm_error(exc: BaseException) -> bool:
    """True for rate limits, timeouts, connection blips, and 5xx/429."""
    name = type(exc).__name__
    if name in {
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "TimeoutError",
        "asyncio.TimeoutError",
    }:
        return True
    msg = str(exc).lower()
    markers = (
        "rate limit",
        "timed out",
        "timeout",
        "deadline exceeded",
        "stream stale",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection error",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "overloaded",
        "server error",
        # Proxies often return HTTP 200 with choices=[] / null on blips.
        "empty choices",
        "provider returned no completion",
        "provider returned an empty response",
        "stream ended with no content",
    )
    return any(m in msg for m in markers)


async def with_llm_retry(
    op: Callable[[], Awaitable[T]],
    *,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay_s: float = _BASE_DELAY_S,
    label: str = "llm",
) -> T:
    """Await ``op``; retry on transient failures with linear backoff.

    Permanent errors (auth, bad request, config) raise immediately.
    """
    last: BaseException | None = None
    n = max(1, int(attempts))
    for i in range(n):
        try:
            return await op()
        except BaseException as exc:
            last = exc
            if i + 1 >= n or not is_transient_llm_error(exc):
                raise
            delay = base_delay_s * (i + 1)
            _logger.warning(
                "%s transient failure (attempt %d/%d): %s — retry in %.2fs",
                label,
                i + 1,
                n,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last


__all__ = ["is_transient_llm_error", "with_llm_retry"]
