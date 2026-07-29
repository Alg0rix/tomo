"""create_agent tool — spawn a swarm member from chat (or any tool caller)."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    """Create an agent; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: create_agent expects a dict of arguments"

    name = arguments.get("name")
    if not isinstance(name, str) or not name.strip():
        return "Error: 'name' is required (e.g. NetOps, Coder)"

    role = arguments.get("role")
    if role is not None and not isinstance(role, str):
        return "Error: 'role' must be a string"
    description = arguments.get("description")
    if description is not None and not isinstance(description, str):
        return "Error: 'description' must be a string"
    model_id = arguments.get("model_id")
    if model_id is not None and not isinstance(model_id, str):
        return "Error: 'model_id' must be a string"
    agent_id = arguments.get("id") or arguments.get("agent_id")
    if agent_id is not None and not isinstance(agent_id, str):
        return "Error: 'id' must be a string"
    workplace_id = arguments.get("workplace_id")
    if workplace_id is not None and not isinstance(workplace_id, str):
        return "Error: 'workplace_id' must be a string"

    data: dict[str, Any] = {
        "name": name.strip(),
        "role": (role or "").strip(),
        "description": (description or "").strip(),
    }
    if model_id and model_id.strip():
        data["model_id"] = model_id.strip()
    if agent_id and agent_id.strip():
        data["id"] = agent_id.strip().lower().replace("-", "_").replace(" ", "_")
    if workplace_id and workplace_id.strip():
        data["workplace_id"] = workplace_id.strip()
        data["workplace_ids"] = [workplace_id.strip()]
        data["workplace_scope"] = "single"

    try:
        from app.services import store

        if workplace_id and workplace_id.strip():
            wp = store.get_workplace(workplace_id.strip())
            if not wp:
                return f"Error: workplace not found: {workplace_id}"

        if model_id and model_id.strip():
            profiles = {p["id"]: p for p in store.list_llm_profiles()}
            if model_id.strip() not in profiles:
                return f"Error: model profile not found: {model_id}"

        agent = store.create_agent(data)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: could not create agent: {exc}"

    aid = agent.get("id") or "?"
    aname = agent.get("name") or aid
    arole = (agent.get("role") or "").strip()
    bits = [f"Created agent **{aname}** `id={aid}`"]
    if arole:
        bits.append(f"role={arole}")
    if agent.get("workplace_id"):
        bits.append(f"workplace={agent['workplace_id']}")
    bits.append("Joined live swarm sessions automatically.")
    bits.append(f"Mention with @{aid} or delegate(agent_id={aid!r}, reason=…).")
    return " ".join(bits)


__all__ = ["run"]
