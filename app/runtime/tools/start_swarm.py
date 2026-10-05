"""Validate a root chat handoff; the web runtime owns swarm execution."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_request: ContextVar[dict[str, str] | None] = ContextVar("start_swarm_user_request", default=None)
ACCEPTED = "Swarm handoff accepted"


def bind_context(user_request: str, *, session_id: str = "", coordinator_id: str = "main") -> Token:
    return _request.set({"request": user_request, "session_id": session_id, "coordinator_id": coordinator_id})


def reset_context(token: Token) -> None:
    try:
        _request.reset(token)
    except ValueError:
        _request.set(None)


def run(arguments: dict[str, Any]) -> str:
    from app.runtime.agent.subagent import current_depth
    from app.runtime.policy import authorize_tool
    authorize_tool("start_swarm", arguments)

    user_request = _request.get()
    if user_request is None or current_depth() > 0:
        return "Error: start_swarm is available only in the root chat turn"
    request = arguments.get("request")
    if not isinstance(request, str) or not request.strip():
        return "Error: provide the complete user task"
    plan = arguments.get("plan")
    if not isinstance(plan, dict):
        return "Error: plan workers in this main chat and provide plan with agents and tasks. No separate planner will run."
    from app.runtime.coordinator.swarm import _accept_plan

    errors: list[str] = []
    if not _accept_plan(plan, session_id=user_request["session_id"], run_id="",
                        coordinator_id=user_request["coordinator_id"], tasks={},
                        validate_only=True, errors=errors):
        return "Error: invalid swarm plan: " + "; ".join(errors) + ". Correct the plan in this chat and retry."
    return ACCEPTED
