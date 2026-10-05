"""``agent_state`` tool — durable cross-session key/value facts per agent.

Admins share one namespace per agent id (existing behavior). Members get a
strictly per-user namespace (``<agent_id>::user::<user_id>``): they can
neither read nor mutate shared agent configuration, and one Member's facts
are invisible to every other account. The scope derives from the bound
execution, never from caller-supplied ids.
"""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import current_agent_id

USER_SCOPE_SEP = "::user::"


def scoped_agent_id(agent_id: str, user_id: str | None, role: str | None) -> str:
    """Storage namespace for ``agent_state`` rows."""
    if role != "admin" and user_id:
        return f"{agent_id}{USER_SCOPE_SEP}{user_id}"
    return agent_id


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    from app.runtime.policy import authorize_tool
    context = authorize_tool("agent_state", arguments)
    action = str(arguments.get("action") or "get").strip().lower()
    requested = str(arguments.get("agent_id") or current_agent_id() or "").strip()
    if not requested:
        return "Error: agent_id is required (no agent bound)"
    from app.services import store

    if context.role != "admin":
        # Members act only inside agents they may currently use, and only
        # inside their own per-user namespace. Shared rows stay untouched.
        try:
            store.access.resolve_context(
                context.user_id, context.session_id, requested, parent=context
            )
        except Exception as exc:
            from app.runtime.access import AccessDenied as _Denied

            return f"Error: {exc}" if isinstance(exc, _Denied) else "Error: agent is unavailable"
        agent_id = scoped_agent_id(requested, context.user_id, context.role)
    else:
        agent_id = requested
    key = str(arguments.get("key") or "").strip()

    from app.services import store

    if action == "list":
        state = store.list_agent_state(agent_id)
        if not state:
            return f"No agent state for {agent_id}"
        return "\n".join(f"{k}: {v}" for k, v in state.items())

    if action == "get":
        if not key:
            return "Error: key is required for get"
        val = store.get_agent_state_value(agent_id, key)
        if val is None:
            return f"No state key {key!r} for {agent_id}"
        return f"{key}: {val}"

    if action == "set":
        if not key:
            return "Error: key is required for set"
        value = arguments.get("value")
        if value is None:
            return "Error: value is required for set"
        store.set_agent_state_value(agent_id, key, str(value))
        return f"Saved agent state {agent_id}.{key}"

    if action == "delete":
        if not key:
            return "Error: key is required for delete"
        ok = store.delete_agent_state_value(agent_id, key)
        return f"Deleted {key}" if ok else f"No state key {key!r}"

    return "Error: action must be list, get, set, or delete"


__all__ = ["run", "scoped_agent_id", "USER_SCOPE_SEP"]
