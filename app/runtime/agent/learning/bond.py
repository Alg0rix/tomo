"""Bond score — pure function over real collaboration aggregates."""

from __future__ import annotations

import math
from typing import Any

# (key, weight, scale, label, how to deepen it). Weights sum to 100.
_PARTS: tuple[tuple[str, float, float, str, str], ...] = (
    ("chats", 25.0, 40.0, "Conversations", "Bring Tomo real work — every chat counts."),
    ("saved_events", 25.0, 15.0, "Lessons kept", "Multi-step tasks give Tomo something worth remembering."),
    ("user_memory_chars", 20.0, 800.0, "About you", "Tell Tomo how you like things done."),
    ("library_skills", 15.0, 10.0, "Shared skills", "Let Tomo turn a repeated workflow into a skill."),
    ("days_active", 15.0, 30.0, "Days together", "Come back tomorrow — steady days deepen the bond."),
)

# (min bond, kanji, romaji, English). Tomo is 友 — "friend" sits at 60.
STAGES: tuple[tuple[int, str, str, str], ...] = (
    (0, "初対面", "shotaimen", "First meeting"),
    (20, "顔見知り", "kaomishiri", "Familiar face"),
    (40, "仲間", "nakama", "Companion"),
    (60, "友達", "tomodachi", "Friend"),
    (80, "親友", "shin'yū", "Best friend"),
)


def _points(weight: float, scale: float, value: int | float) -> float:
    return weight * math.tanh(max(0, value) / scale)


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
    values = {
        "chats": chats,
        "saved_events": saved_events,
        "user_memory_chars": user_memory_chars,
        "library_skills": library_skills,
        "days_active": days_active,
    }
    raw = sum(_points(w, s, values[k]) for k, w, s, _, _ in _PARTS)
    return int(max(0, min(100, round(raw))))


def bond_breakdown(**values: int) -> list[dict[str, Any]]:
    """Per-part contribution, in the same order as the formula."""
    out = []
    for key, weight, scale, label, hint in _PARTS:
        value = int(values.get(key) or 0)
        pts = _points(weight, scale, value)
        out.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "points": round(pts, 1),
                "max": int(weight),
                "ratio": round(pts / weight, 3),
                "hint": hint,
            }
        )
    return out


def bond_stage(bond: int, parts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Current stage, the next one, and the part with the most room to grow."""
    idx = max(i for i, s in enumerate(STAGES) if bond >= s[0])
    floor, kanji, romaji, name = STAGES[idx]
    nxt = STAGES[idx + 1] if idx + 1 < len(STAGES) else None
    ceiling = nxt[0] if nxt else 100
    span = max(1, ceiling - floor)
    growth = None
    if parts:
        best = max(parts, key=lambda p: p["max"] - p["points"])
        if best["max"] - best["points"] >= 1:
            growth = {"key": best["key"], "label": best["label"], "hint": best["hint"]}
    return {
        "level": idx + 1,
        "levels": len(STAGES),
        "kanji": kanji,
        "romaji": romaji,
        "name": name,
        "floor": floor,
        "progress": round(min(1.0, (bond - floor) / span), 3),
        "next": (
            {"kanji": nxt[1], "name": nxt[3], "at": nxt[0], "to_go": nxt[0] - bond}
            if nxt
            else None
        ),
        "grow": growth,
    }


__all__ = ["STAGES", "bond_breakdown", "bond_stage", "compute_bond"]
