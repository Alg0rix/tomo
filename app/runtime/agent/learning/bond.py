"""Bond score — pure function over real collaboration aggregates."""

from __future__ import annotations

import math


def compute_bond(
    *,
    chats: int = 0,
    saved_events: int = 0,
    user_memory_chars: int = 0,
    library_skills: int = 0,
    days_active: int = 0,
) -> int:
    """Return bond in 0..100 from the documented tanh blend.

    bond = clamp(0, 100, round(
        25 * tanh(chats / 40)
      + 25 * tanh(saved_events / 15)
      + 20 * tanh(user_memory_chars / 800)
      + 15 * tanh(library_skills / 10)
      + 15 * tanh(days_active / 30)
    ))
    """
    raw = (
        25.0 * math.tanh(max(0, chats) / 40.0)
        + 25.0 * math.tanh(max(0, saved_events) / 15.0)
        + 20.0 * math.tanh(max(0, user_memory_chars) / 800.0)
        + 15.0 * math.tanh(max(0, library_skills) / 10.0)
        + 15.0 * math.tanh(max(0, days_active) / 30.0)
    )
    return int(max(0, min(100, round(raw))))


__all__ = ["compute_bond"]
