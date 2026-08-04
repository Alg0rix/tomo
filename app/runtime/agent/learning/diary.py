"""Diary line extraction / synthesis for learning reviews."""

from __future__ import annotations

import re

_DIARY_RE = re.compile(
    r"(?im)^\s*diary\s*:\s*(.+?)(?:\n\n|\Z)",
    re.DOTALL,
)
_DIARY_INLINE_RE = re.compile(r"(?i)diary\s*:\s*(.+)")


def extract_diary_line(note: str | None) -> str:
    """Pull a Diary: line from the reviewer's final note, if present."""
    text = (note or "").strip()
    if not text:
        return ""
    m = _DIARY_RE.search(text)
    if m:
        return " ".join(m.group(1).strip().split())
    # Single-line fallback
    for line in text.splitlines():
        m2 = _DIARY_INLINE_RE.match(line.strip())
        if m2:
            return " ".join(m2.group(1).strip().split())
    return ""


def synthesize_diary_from_actions(actions: list[str] | None) -> str:
    """Human-readable fallback when the model omits a Diary: line."""
    acts = [a.strip() for a in (actions or []) if isinstance(a, str) and a.strip()]
    if not acts:
        return ""
    # Keep short
    parts: list[str] = []
    for a in acts[:6]:
        parts.append(a.splitlines()[0][:120])
    joined = "; ".join(parts)
    if len(joined) > 280:
        joined = joined[:277] + "…"
    return f"Recorded: {joined}"


def derive_diary(*, saved: bool, note: str | None, actions: list[str] | None) -> str:
    if not saved:
        return ""
    return extract_diary_line(note) or synthesize_diary_from_actions(actions)


__all__ = [
    "extract_diary_line",
    "synthesize_diary_from_actions",
    "derive_diary",
]
