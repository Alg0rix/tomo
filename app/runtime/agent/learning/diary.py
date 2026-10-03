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


def describe_learned(item: dict | None) -> str:
    """One plain sentence for a classified review write, or "" when unknown."""
    if not isinstance(item, dict) or not item.get("saved_eligible"):
        return ""
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    tool = item.get("tool")
    if tool == "memory":
        entity = detail.get("entity") or "memory"
        where = "your profile" if entity == "user/profile" else entity
        if detail.get("action") == "remove":
            return f"Let go of an outdated note on {where}."
        content = detail.get("content")
        if not content:
            return f"Updated {where}."
        verb = "Corrected" if detail.get("action") == "replace" else "Noted"
        return f"{verb} on {where}: {content}"
    if tool == "manage_skill":
        sid = detail.get("name") or detail.get("skill_id") or "a skill"
        verb = {"create": "Wrote a new skill", "merge": "Merged skills into",
                "delete": "Retired the skill"}.get(detail.get("action", ""), "Refined the skill")
        return f"{verb} '{sid}'."
    if tool == "record_episode":
        title = detail.get("title")
        return f"Remembered the episode: {title}" if title else "Remembered how a task went."
    if tool == "save_artifact":
        return f"Kept a file: {detail.get('name')}" if detail.get("name") else "Kept a file for later."
    if tool == "agent_state":
        key = detail.get("key")
        return f"Updated working state '{key}'." if key else "Updated working state."
    return ""


def synthesize_diary_from_items(items: list[dict] | None) -> str:
    lines = [line for line in (describe_learned(i) for i in items or []) if line]
    if not lines:
        return ""
    text = " ".join(lines[:3])
    return text if len(text) <= 280 else text[:279] + "…"


def derive_diary(
    *,
    saved: bool,
    note: str | None,
    actions: list[str] | None,
    items: list[dict] | None = None,
) -> str:
    if not saved:
        return ""
    return (
        extract_diary_line(note)
        or synthesize_diary_from_items(items)
        or synthesize_diary_from_actions(actions)
    )


__all__ = [
    "describe_learned",
    "extract_diary_line",
    "synthesize_diary_from_actions",
    "synthesize_diary_from_items",
    "derive_diary",
]
