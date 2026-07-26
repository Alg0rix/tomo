"""Task routing and membership-safe agent selection."""

from __future__ import annotations

import re
from typing import Any

_LEADING_MENTION = re.compile(r"^@(\S+)\s*(.*)$", re.DOTALL)


def resolve_target(
    *,
    agent_ids: list[str],
    agents: list[dict[str, Any]],
    query: str,
) -> str | None:
    """Resolve ``query`` to a session member agent id, or ``None``.

    Matches by agent id, display name (casefold), or ``@name`` / ``@id``.
    Non-members are never returned even if present in ``agents``.
    """
    raw = (query or "").strip()
    if not raw:
        return None
    needle = raw.lstrip("@").casefold()
    if not needle:
        return None

    member_set = {aid for aid in agent_ids if isinstance(aid, str) and aid}
    by_id = {
        str(a["id"]): a
        for a in agents
        if isinstance(a, dict) and isinstance(a.get("id"), str) and a["id"] in member_set
    }

    for aid in member_set:
        if aid.casefold() == needle:
            return aid

    for aid, agent in by_id.items():
        name = agent.get("name")
        if isinstance(name, str) and name.casefold() == needle:
            return aid

    return None


def parse_leading_mention(text: str) -> tuple[str | None, str]:
    """Split a leading ``@handle`` from ``text``.

    Returns ``(handle, rest)`` when present, else ``(None, text)``.
    """
    if not isinstance(text, str):
        return None, ""
    match = _LEADING_MENTION.match(text.lstrip())
    if not match:
        return None, text
    handle = match.group(1)
    rest = match.group(2)
    return handle, rest


__all__ = ["resolve_target", "parse_leading_mention"]
