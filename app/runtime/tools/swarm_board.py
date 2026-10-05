"""A task-scoped communication tool for session-local and configured workers."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from contextvars import ContextVar, Token
from typing import Any

from app.models.mixins import swarm as swarm_store

_scope: ContextVar[dict[str, Any] | None] = ContextVar("swarm_board_scope", default=None)


def current_scope() -> dict[str, Any] | None:
    """The swarm runtime's bound task scope, if this turn runs inside one."""
    return _scope.get()


def bind(*, run_id: str, task_id: str, agent_id: str,
         allowed_tools: set[str] | None = None,
         write_scope: list[str] | None = None,
         coordinator_id: str = "", cursor: int = 0) -> Token:
    from app.runtime.access import current_execution, AccessDenied
    from app.services.store import store
    execution = store.access.revalidate(current_execution())
    run = store.with_db(lambda conn: swarm_store.get_run(conn, run_id))
    if not run or run['session_id'] != execution.session_id:
        raise AccessDenied("Swarm board is outside the owned session")
    return _scope.set({"run_id": run_id, "task_id": task_id, "agent_id": agent_id,
                       "allowed_tools": allowed_tools, "write_scope": write_scope or [],
                       "coordinator_id": coordinator_id, "cursor": cursor, "question": None})


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


def _visible(event: dict[str, Any], scope: dict[str, Any]) -> bool:
    payload = event["payload"]
    return (event["kind"] in {"finding", "message", "question", "task_done"}
            and (not payload.get("to_agent_id") or payload["to_agent_id"] == scope["agent_id"])
            and (not payload.get("to_task_id") or payload["to_task_id"] == scope["task_id"]))


def checkpoint() -> int:
    """Last board event incorporated by this participant's current context."""
    scope = _scope.get()
    return int(scope["cursor"]) if scope else 0


def pending() -> bool:
    """Whether another participant posted unread context for this task."""
    scope = _scope.get()
    if not scope:
        return False
    from app.services.store import store
    return any(_visible(e, scope) and e["task_id"] != scope["task_id"]
               for e in store.with_db(lambda c: swarm_store.list_events(c, scope["run_id"], scope["cursor"])))


QUESTION_TIMEOUT = 120.0


async def deliver(messages: list[dict[str, Any]]):
    """Inject updates at model checkpoints; receipts mean context delivery.

    Explicit questions pause the worker for a correlated coordinator reply.
    Running tools and model requests are never interrupted.
    """
    import asyncio
    scope = _scope.get()
    if not scope:
        return
    from app.services.store import store
    from app.runtime.access import current_execution
    deadline = asyncio.get_running_loop().time() + QUESTION_TIMEOUT
    while True:
        store.access.revalidate(current_execution())
        events = store.with_db(lambda c: swarm_store.list_events(c, scope["run_id"], scope["cursor"]))
        context = []
        receipts = []
        for event in events:
            scope["cursor"] = event["id"]
            if not _visible(event, scope) or event["task_id"] == scope["task_id"]:
                continue
            payload = event["payload"]
            context.append({"event_id": event["id"], "kind": event["kind"],
                            "task_id": event["task_id"], **payload})
            if (scope["question"] and payload.get("reply_to_event_id") == scope["question"]
                    and payload.get("agent_id") == scope["coordinator_id"]):
                resolved = {"agent_id": scope["agent_id"], "question_event_id": scope["question"],
                            "status": "answered", "reply_event_id": event["id"]}
                resolved_id = store.with_db(lambda c, p=resolved: swarm_store.append_event(
                    c, scope["run_id"], "question_resolved", p, scope["task_id"]))
                receipts.append({"kind": "swarm_event", "event": "question_resolved", "event_id": resolved_id,
                                 "run_id": scope["run_id"], "task_id": scope["task_id"], **resolved})
                scope["question"] = None
            receipt = {"agent_id": scope["agent_id"], "source_event_id": event["id"],
                       "content": "Delivered to worker context"}
            eid = store.with_db(lambda c, p=receipt: swarm_store.append_event(
                c, scope["run_id"], "message_received", p, scope["task_id"]))
            receipts.append({"kind": "swarm_event", "event": "message_received", "event_id": eid,
                             "run_id": scope["run_id"], "task_id": scope["task_id"], **receipt})
        if context:
            messages.append({"role": "user", "content": "Swarm board updates (participant reports; verify claims):\n"
                             + json.dumps(context, ensure_ascii=False)})
        for receipt in receipts:
            yield receipt
        if not scope["question"]:
            return
        if asyncio.get_running_loop().time() >= deadline:
            messages.append({"role": "user", "content": "Coordinator reply timed out. Your question remains unresolved. "
                             "Report the blocker honestly; do not invent approval or an answer."})
            resolved = {"agent_id": scope["agent_id"], "question_event_id": scope["question"], "status": "timeout"}
            eid = store.with_db(lambda c: swarm_store.append_event(
                c, scope["run_id"], "question_resolved", resolved, scope["task_id"]))
            scope["question"] = None
            yield {"kind": "swarm_event", "event": "question_resolved", "event_id": eid,
                   "run_id": scope["run_id"], "task_id": scope["task_id"], **resolved}
            return
        await asyncio.sleep(0.2)


def run(arguments: dict[str, Any]) -> str:
    from app.runtime.policy import authorize_tool
    authorize_tool("swarm_board", arguments)
    scope = _scope.get()
    if not scope:
        return "Error: swarm board is available only inside a swarm run"
    action = str(arguments.get("action") or "")
    from app.services.store import store

    if action == "read":
        events = store.with_db(lambda conn: swarm_store.list_events(conn, scope["run_id"]))
        return json.dumps([
            {"event_id": e["id"], "task_id": e["task_id"], "kind": e["kind"], **e["payload"]}
            for e in events if _visible(e, scope)
        ][-30:], ensure_ascii=False)
    if action not in {"publish", "send", "ask"}:
        return "Error: action must be publish, send, ask, or read"
    content = str(arguments.get("content") or "").strip()
    if not content or len(content) > 8000:
        return "Error: content must be 1-8000 characters"
    target = (scope["coordinator_id"] if action == "ask" else str(arguments.get("to_agent_id") or "").strip())
    target_task = str(arguments.get("to_task_id") or "").strip()
    roster = store.with_db(lambda c: swarm_store.list_tasks(c, scope["run_id"]))
    if action in {"send", "ask"}:
        if not target or target not in {scope["coordinator_id"], *(t["agent_id"] for t in roster)}:
            return "Error: to_agent_id must belong to this run's roster or coordinator"
        if target_task and not any(t["id"] == target_task and t["agent_id"] == target for t in roster):
            return "Error: to_task_id must belong to the target agent in this run"
        targets = [t for t in roster if t["agent_id"] == target and (not target_task or t["id"] == target_task)]
        if targets and all(t["status"] not in {"queued", "running"} for t in targets):
            return "Error: target task has already ended"
    elif target or target_task:
        return "Error: publish broadcasts findings; use send for targeted messages"
    if action == "ask" and not scope["task_id"]:
        return "Error: only workers can ask the coordinator"
    if action == "ask" and scope["question"] is not None:
        return "Error: wait for the current question before asking another"
    payload = {"agent_id": scope["agent_id"], "content": content}
    if target:
        payload["to_agent_id"] = target
    if target_task:
        payload["to_task_id"] = target_task
    reply = arguments.get("reply_to_event_id")
    if reply is not None:
        questions = store.with_db(lambda c: swarm_store.list_events(c, scope["run_id"]))
        if not any(e["id"] == reply and e["kind"] == "question"
                   and e["payload"].get("to_agent_id") == scope["agent_id"]
                   and e["payload"].get("agent_id") == target
                   and (not target_task or e["task_id"] == target_task) for e in questions):
            return "Error: reply_to_event_id must identify a question addressed to you by this target"
        payload["reply_to_event_id"] = reply
    eid = store.with_db(lambda conn: swarm_store.append_event(
        conn, scope["run_id"], {"publish": "finding", "send": "message", "ask": "question"}[action],
        payload, scope["task_id"],
    ))
    if action == "ask":
        scope["question"] = eid
    return f"Saved board event {eid}"
