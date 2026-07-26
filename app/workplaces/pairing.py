"""Pairing codes and rate limits for Tomo Connector."""

from __future__ import annotations

import secrets
import string
import threading
import time
from collections import defaultdict

# Short codes are easy to type; 6 chars from Crockford-ish alphabet (~30 bits).
_ALPHABET = string.ascii_uppercase + string.digits
_PAIRING_TTL_SECONDS = 30 * 60  # 30 minutes
_MAX_ATTEMPTS_PER_WINDOW = 20
_WINDOW_SECONDS = 300


class PairingRateLimiter:
    """Simple in-memory rate limit for pair attempts (per IP or global key)."""

    def __init__(
        self,
        max_attempts: int = _MAX_ATTEMPTS_PER_WINDOW,
        window_seconds: float = _WINDOW_SECONDS,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            bucket = self._hits[key]
            cutoff = now - self.window_seconds
            self._hits[key] = [t for t in bucket if t >= cutoff]
            if len(self._hits[key]) >= self.max_attempts:
                return False
            self._hits[key].append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def generate_pairing_code(length: int = 6) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def pairing_ttl_seconds() -> float:
    return float(_PAIRING_TTL_SECONDS)


def pairing_expires_at(now: float | None = None) -> float:
    return (now if now is not None else time.time()) + pairing_ttl_seconds()


rate_limiter = PairingRateLimiter()

__all__ = [
    "PairingRateLimiter",
    "generate_pairing_code",
    "pairing_ttl_seconds",
    "pairing_expires_at",
    "rate_limiter",
]
