"""``agent_state`` tool — durable cross-session key/value facts per agent."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import current_agent_id


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    action = str(arguments.get("action") or "get").strip().lower()
    agent_id = str(arguments.get("agent_id") or current_agent_id() or "").strip()
    if not agent_id:
        return "Error: agent_id is required (no agent bound)"
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


__all__ = ["run"]
