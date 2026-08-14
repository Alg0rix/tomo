"""Stable, safe MCP tool ID normalization: ``mcp__<server>__<tool>``."""

from __future__ import annotations

import hashlib
import re

_UNSAFE_RE = re.compile(r"[^a-zA-Z0-9_]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_MAX_LEN = 64
_PREFIX = "mcp__"


def _sanitize(part: str) -> str:
    """Collapse ``part`` to ``[a-zA-Z0-9_]+`` with no doubled underscores.

    No doubled underscores keeps ``mcp__<server>__<tool>`` splittable on the
    first ``__`` after the fixed prefix without ambiguity.
    """
    cleaned = _UNSAFE_RE.sub("_", part).strip("_")
    cleaned = _MULTI_UNDERSCORE_RE.sub("_", cleaned)
    return cleaned or "x"


def runtime_tool_id(server_id: str, tool_name: str) -> str:
    """Return the namespaced, length-bounded runtime tool id.

    Deterministically shortened (hash suffix) when the natural form would
    exceed ``_MAX_LEN`` — callers recover the exact original MCP tool name
    from the stored ``mcp_items`` row by this id, not by re-parsing it.
    """
    server = _sanitize(server_id)
    tool = _sanitize(tool_name)
    runtime_id = f"{_PREFIX}{server}__{tool}"
    if len(runtime_id) <= _MAX_LEN:
        return runtime_id

    digest = hashlib.sha1(f"{server}__{tool}".encode("utf-8")).hexdigest()[:8]
    budget = _MAX_LEN - len(_PREFIX) - len("__") - len(digest) - 1
    budget = max(budget, 8)
    server_short = server[: max(budget // 3, 4)]
    tool_short = tool[: max(budget - len(server_short), 4)]
    return f"{_PREFIX}{server_short}__{tool_short}_{digest}"


def split_runtime_tool_id(runtime_id: str) -> tuple[str, str] | None:
    """Split a namespaced id back into ``(server_id, tool_part)``.

    ``tool_part`` is the sanitized/possibly-shortened form, not necessarily
    the exact original MCP tool name — look that up via the ``mcp_items``
    row's ``runtime_id`` column for dispatch.
    """
    if not runtime_id.startswith(_PREFIX):
        return None
    rest = runtime_id[len(_PREFIX):]
    parts = rest.split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def is_mcp_runtime_id(name: str) -> bool:
    return isinstance(name, str) and name.startswith(_PREFIX)


__all__ = ["runtime_tool_id", "split_runtime_tool_id", "is_mcp_runtime_id"]
