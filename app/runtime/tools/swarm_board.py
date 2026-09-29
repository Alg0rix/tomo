"""A task-scoped communication tool for session-local and configured workers."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from contextvars import ContextVar, Token
from typing import Any

from app.models.mixins import swarm as swarm_store

_scope: ContextVar[dict[str, str] | None] = ContextVar("swarm_board_scope", default=None)


def bind(*, run_id: str, task_id: str, agent_id: str,
         allowed_tools: set[str] | None = None,
         write_scope: list[str] | None = None) -> Token:
    return _scope.set({"run_id": run_id, "task_id": task_id, "agent_id": agent_id,
                       "allowed_tools": allowed_tools, "write_scope": write_scope or []})


def reset(token: Token) -> None:
    _scope.reset(token)


def authorize(name: str, arguments: dict[str, Any]) -> str | None:
    """Return a denial for out-of-scope worker tools and file writes."""
    scope = _scope.get()
    if not scope:
        return None
    allowed = scope.get("allowed_tools")
    if allowed is not None and name not in allowed:
        return f"Error: tool '{name}' is outside this worker's assigned capabilities"
    if name not in {"write_file", "str_replace", "patch", "delete_file"}:
        return None
    raw = str(arguments.get("path") or "")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        return "Error: write path must be a relative path inside the assigned scope"
    scopes = scope.get("write_scope") or []
    if not any(path == PurePosixPath(s) or PurePosixPath(s) in path.parents for s in scopes):
        return "Error: write path is outside this worker's assigned scope"
    return None


def run(arguments: dict[str, Any]) -> str:
    scope = _scope.get()
    if not scope:
        return "Error: swarm board is available only inside a swarm task"
    action = str(arguments.get("action") or "")
    from app.services.store import store

    if action == "read":
        events = store.with_db(lambda conn: swarm_store.list_events(conn, scope["run_id"]))
        visible = [
            {"kind": e["kind"], "agent_id": e["payload"].get("agent_id"),
             "content": e["payload"].get("content"), "to_agent_id": e["payload"].get("to_agent_id")}
            for e in events if e["kind"] in {"finding", "message", "task_done"}
            and (not e["payload"].get("to_agent_id")
                 or e["payload"].get("to_agent_id") == scope["agent_id"])
        ]
        return json.dumps(visible[-30:], ensure_ascii=False)
    if action not in {"publish", "send"}:
        return "Error: action must be publish, send, or read"
    content = str(arguments.get("content") or "").strip()
    if not content or len(content) > 8000:
        return "Error: content must be 1-8000 characters"
    target = str(arguments.get("to_agent_id") or "").strip()
    if action == "send" and not target:
        return "Error: to_agent_id is required"
    payload = {"agent_id": scope["agent_id"], "content": content}
    if target:
        payload["to_agent_id"] = target
    eid = store.with_db(lambda conn: swarm_store.append_event(
        conn, scope["run_id"], "finding" if action == "publish" else "message",
        payload, scope["task_id"],
    ))
    return f"Saved board event {eid}"
