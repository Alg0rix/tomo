"""Task routing and membership-safe agent selection."""

from __future__ import annotations

import re
from typing import Any

_LEADING_MENTION = re.compile(r"^@([^\s@]+)\s*(.*)$", re.DOTALL)
# Mid-message mentions (first only for force-handoff we use leading).
_ANY_MENTION = re.compile(r"@([^\s@]+)")


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", (s or "").casefold())


def resolve_target(
    *,
    agent_ids: list[str],
    agents: list[dict[str, Any]],
    query: str,
) -> str | None:
    """Resolve ``query`` to a session member agent id, or ``None``.

    Match order: exact id → exact name → exact role → unique prefix on
    id/name/role. ``@`` prefix is optional. Non-members never returned.
    """
    raw = (query or "").strip()
    if not raw:
        return None
    needle = raw.lstrip("@").strip()
    if not needle:
        return None
    needle_cf = needle.casefold()
    needle_n = _norm(needle)

    member_set = {aid for aid in agent_ids if isinstance(aid, str) and aid}
    by_id = {
        str(a["id"]): a
        for a in agents
        if isinstance(a, dict)
        and isinstance(a.get("id"), str)
        and a["id"] in member_set
    }

    # Exact id
    for aid in member_set:
        if aid.casefold() == needle_cf or _norm(aid) == needle_n:
            return aid

    # Exact name
    for aid, agent in by_id.items():
        name = agent.get("name")
        if isinstance(name, str) and (
            name.casefold() == needle_cf or _norm(name) == needle_n
        ):
            return aid

    # Exact role
    for aid, agent in by_id.items():
        role = agent.get("role")
        if isinstance(role, str) and role.strip() and (
            role.casefold() == needle_cf or _norm(role) == needle_n
        ):
            return aid

    # Unique prefix (id / name / role)
    prefix_hits: list[str] = []
    for aid, agent in by_id.items():
        candidates = [aid]
        for key in ("name", "role"):
            val = agent.get(key)
            if isinstance(val, str) and val.strip():
                candidates.append(val)
        for c in candidates:
            cf = c.casefold()
            n = _norm(c)
            if cf.startswith(needle_cf) or n.startswith(needle_n):
                if aid not in prefix_hits:
                    prefix_hits.append(aid)
                break
    if len(prefix_hits) == 1:
        return prefix_hits[0]

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


def list_mentions(text: str) -> list[str]:
    """Return all ``@handle`` tokens in ``text`` (without the @)."""
    if not isinstance(text, str):
        return []
    return _ANY_MENTION.findall(text)


__all__ = ["resolve_target", "parse_leading_mention", "list_mentions"]
