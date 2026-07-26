"""Delegate tool — hand a turn to another session member.

Membership is resolved against a process-local context bound by the web/loop
layer before ``run_turn`` (session ``agent_ids`` + agent records). Without
context, or when the target is not a member, returns an ``Error:`` string —
never raises.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from app.runtime.coordinator.router import resolve_target

_Context = dict[str, Any]
_ctx: ContextVar[_Context | None] = ContextVar("delegate_session_ctx", default=None)


def bind_context(
    *,
    agent_ids: list[str],
    agents: list[dict[str, Any]],
) -> Token:
    """Bind session membership for subsequent ``delegate`` tool calls."""
    return _ctx.set({"agent_ids": list(agent_ids), "agents": list(agents)})


def reset_context(token: Token | None = None) -> None:
    """Clear or reset the bound session context."""
    if token is not None:
        _ctx.reset(token)
    else:
        _ctx.set(None)


def run(arguments: dict[str, Any]) -> str:
    """Tool backend: resolve target within the bound session and confirm handoff."""
    if not isinstance(arguments, dict):
        return "Error: delegate expects a dict of arguments"

    ctx = _ctx.get()
    if not ctx:
        return "Error: no session context for delegate"

    query = arguments.get("agent_id") or arguments.get("name") or arguments.get("agent")
    if not isinstance(query, str) or not query.strip():
        return "Error: delegate requires agent_id or name"

    target = resolve_target(
        agent_ids=ctx.get("agent_ids") or [],
        agents=ctx.get("agents") or [],
        query=query,
    )
    if not target:
        return f"Error: '{query.strip()}' is not a member of this session"

    return f"Delegated to {target}"


def parse_delegated_id(result: str) -> str | None:
    """Extract target id from a successful ``Delegated to {id}`` result string."""
    prefix = "Delegated to "
    if isinstance(result, str) and result.startswith(prefix):
        target = result[len(prefix) :].strip()
        return target or None
    return None


__all__ = [
    "bind_context",
    "reset_context",
    "run",
    "parse_delegated_id",
]
