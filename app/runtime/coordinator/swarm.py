"""Dynamic, opt-in orchestration for a single chat turn.

The model proposes workers and tasks; code owns identity, access, scheduling,
state transitions, cancellation, and the final handoff back to the coordinator.
Workers can be configured agents or session-local instances created on demand.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any

from app.models.mixins import swarm as db
from app.runtime.agent.context import build_system_prompt
from app.runtime.agent.loop import _emit_drained_steers, run_turn
from app.runtime.agent.subagent import bind_depth, current_depth, reset_depth
from app.runtime.tools import swarm_board
from app.runtime.tools.registry import get_openai_tools
from app.services.store import store

log = logging.getLogger(__name__)
MAX_ACTIVE = 4
MAX_TASKS = 12
REVIEW_INTERVAL = 30.0
# Worker creation belongs to the coordinator's bounded scheduler. Other
# capabilities are selected per task from the agent's enabled tool catalog.
_ORCHESTRATION_TOOLS = {"delegate", "create_agent", "start_swarm"}


def _event(run_id: str, kind: str, payload: dict[str, Any], task_id: str = "") -> dict[str, Any]:
    eid = store.with_db(lambda conn: db.append_event(conn, run_id, kind, payload, task_id))
    return {"kind": "swarm_event", "run_id": run_id, "event_id": eid,
            "event": kind, "task_id": task_id, **payload}


def _scope_valid(scopes: Any) -> bool:
    return isinstance(scopes, list) and len(scopes) <= 8 and all(
        isinstance(s, str) and 0 < len(s) <= 200 and not PurePosixPath(s).is_absolute()
        and ".." not in PurePosixPath(s).parts for s in scopes
    )


def _overlap(a: list[str], b: list[str]) -> bool:
    return any(
        x == y or x.startswith(y.rstrip("/") + "/") or y.startswith(x.rstrip("/") + "/")
        for x in a for y in b
    )


def _accept_plan(
    plan: dict[str, Any], *, session_id: str, run_id: str,
    coordinator_id: str, tasks: dict[str, dict[str, Any]],
    validate_only: bool = False, errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Validate proposed agents/tasks and persist only safe, new work."""
    def reject(reason: str) -> list[dict[str, Any]]:
        if errors is not None:
            errors.append(reason)
        return []

    from app.runtime.access import current_execution, AccessDenied
    from app.runtime.policy import authorized_agents, filter_schemas
    context = store.access.revalidate(current_execution())
    if context.session_id != session_id:
        raise AccessDenied("Swarm cannot switch sessions")
    raw_agents = plan.get("agents") or []
    raw_tasks = plan.get("tasks") or []
    if not isinstance(raw_agents, list) or len(raw_agents) > MAX_ACTIVE:
        return reject('Too many agents or agents is not an array.')
    if not isinstance(raw_tasks, list) or not raw_tasks or len(raw_tasks) > MAX_TASKS - len(tasks):
        return reject('Provide a non-empty tasks array within the remaining task budget.')
    # Validate the dependency graph before creating session-local agents.
    raw_keys = [str(t.get("key") or "").strip()[:80] for t in raw_tasks if isinstance(t, dict)]
    if len(raw_keys) != len(raw_tasks) or len(set(raw_keys)) != len(raw_keys) or not all(raw_keys):
        return reject('Each task needs a unique non-empty key.')
    valid_keys = set(tasks) | set(raw_keys)
    if any(
        not isinstance(t.get("depends_on") or [], list)
        or any(not isinstance(d, str) or d not in valid_keys or d == t["key"]
               for d in (t.get("depends_on") or []))
        or not _scope_valid(t.get("write_scope") or [])
        for t in raw_tasks
    ):
        return reject('Task dependencies must exist and scopes must be relative paths.')
    unresolved_keys = {
        key: set(raw.get("depends_on") or []) - set(tasks)
        for key, raw in zip(raw_keys, raw_tasks)
    }
    resolved_keys = set(tasks)
    while unresolved_keys:
        ready_keys = {key for key, deps in unresolved_keys.items() if deps <= resolved_keys}
        if not ready_keys:
            return reject('Task dependencies contain a cycle.')
        resolved_keys.update(ready_keys)
        for key in ready_keys:
            unresolved_keys.pop(key)
    allowed_ids = {a["id"] for a in authorized_agents(context)}
    configured = {a["id"]: a for a in store.list_agents() if a.get("enabled") and a["id"] in allowed_ids}
    local = {a["id"]: a for a in store.with_db(lambda conn: db.list_agents(conn, session_id))}
    local = {key: a for key, a in local.items() if a["base_agent_id"] in allowed_ids}
    names = {a["name"]: a["id"] for a in local.values()}
    candidate_names = set(names)
    new_agent_bases: dict[str, str] = {}
    for raw in raw_agents:
        if not isinstance(raw, dict):
            return reject('Each agent must be an object.')
        name = str(raw.get("name") or "").strip()[:80]
        purpose = str(raw.get("purpose") or "").strip()
        base = str(raw.get("base_agent_id") or coordinator_id).strip()
        if not name or not purpose or base not in configured:
            return reject('Each new agent needs a name, purpose, and enabled base_agent_id.')
        candidate_names.add(name)
        new_agent_bases[name] = base
    enabled_tool_cache: dict[str, set[str]] = {}

    def enabled_tools(base_agent_id: str) -> set[str]:
        if base_agent_id not in enabled_tool_cache:
            enabled_tool_cache[base_agent_id] = {
                s.get("function", {}).get("name")
                for s in filter_schemas(store.access.resolve_context(context.user_id, session_id, base_agent_id, parent=context),
                                        store.get_agent_openai_tools(base_agent_id))
                if s.get("function", {}).get("name") not in _ORCHESTRATION_TOOLS
            } | {"swarm_board"}
        return enabled_tool_cache[base_agent_id]

    for raw in raw_tasks:
        aid = str(raw.get("agent_id") or "").strip()
        if (not str(raw.get("brief") or "").strip() or aid == coordinator_id
                or aid not in configured and aid not in local and aid not in candidate_names):
            return reject('Each task needs a brief and a valid worker ID or declared name; the coordinator itself cannot be a worker.')
        base = new_agent_bases.get(aid) or (
            local[names[aid]]["base_agent_id"] if aid in names else
            local[aid]["base_agent_id"] if aid in local else aid
        )
        selected = raw.get("tools")
        if selected is not None and (
            not isinstance(selected, list)
            or len(selected) > 64
            or any(not isinstance(tool, str) or tool not in enabled_tools(base)
                   for tool in selected)
        ):
            return reject('Selected tools must belong to the worker template enabled catalog.')
    if validate_only:
        return [{"validated": True}]
    for raw in raw_agents:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:80]
        purpose = str(raw.get("purpose") or "").strip()[:500]
        instructions = str(raw.get("instructions") or "").strip()[:4000]
        base = str(raw.get("base_agent_id") or coordinator_id).strip()
        if not name or not purpose or base not in configured:
            continue
        agent = store.with_db(lambda conn: db.create_agent(
            conn, session_id=session_id, name=name, purpose=purpose,
            instructions=instructions, base_agent_id=base,
        ))
        local[agent["id"]] = agent
        names[name] = agent["id"]
    proposed: list[dict[str, Any]] = []
    new_keys: set[str] = set()
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()[:80]
        aid = str(raw.get("agent_id") or "").strip()
        aid = names.get(aid, aid)
        base = local[aid]["base_agent_id"] if aid in local else aid
        selected = raw.get("tools")
        tools = list(dict.fromkeys(selected)) if selected is not None else sorted(enabled_tools(base))
        brief = str(raw.get("brief") or "").strip()[:4000]
        deps = raw.get("depends_on") or []
        scope = raw.get("write_scope") or []
        if (
            not key or key in tasks or key in new_keys or not brief
            or aid == coordinator_id or aid not in configured and aid not in local
            or not isinstance(deps, list) or not all(isinstance(d, str) for d in deps)
            or not _scope_valid(scope)
        ):
            continue
        proposed.append({"key": key, "agent_id": aid, "brief": brief,
                         "depends_on": deps, "write_scope": scope,
                         "tools": tools,
                         "agent_name": local[aid]["name"] if aid in local else configured[aid]["name"],
                         "purpose": local[aid]["purpose"] if aid in local else (
                             configured[aid].get("role") or configured[aid].get("description") or ""),
                         "base_agent_id": local[aid]["base_agent_id"] if aid in local else aid,
                         "instructions": local[aid]["instructions"] if aid in local else "",
                         "context_summary": local[aid]["context_summary"] if aid in local else "",
                         "dynamic": aid in local})
        new_keys.add(key)
    for task in proposed:
        task["id"] = store.with_db(lambda conn, t=task: db.create_task(
            conn, run_id=run_id, agent_id=t["agent_id"], brief=t["brief"],
            depends_on=t["depends_on"], write_scope=t["write_scope"],
            tools=t["tools"],
        ))
        task["status"] = "queued"
        task["result"] = ""
    return proposed


async def _worker(
    *, task: dict[str, Any], request: str, session_id: str, run_id: str,
    dependencies: dict[str, Any], events: asyncio.Queue,
) -> None:
    tid = task["id"]
    aid = task["agent_id"]
    base = task["base_agent_id"]
    result = ""
    status = "done"
    token = None
    depth_token = bind_depth(current_depth() + 1)
    try:
        store.with_db(lambda conn: db.update_task(conn, tid, "running"))
        await events.put(("task_started", task, {}))
        available = {
            s.get("function", {}).get("name"): s
            for s in store.get_agent_openai_tools(base)
        }
        selected = set(task["tools"]) - {"swarm_board"}
        missing = selected - available.keys()
        if missing:
            raise RuntimeError(f"Assigned tools are no longer available: {', '.join(sorted(missing))}")
        allowed = selected | {"swarm_board"}
        # Bind before provisioning schemas: the runtime grants swarm_board per
        # worker task at bind time, and schema filtering honors that grant.
        # The bind itself verifies the run's session ownership.
        token = swarm_board.bind(run_id=run_id, task_id=tid, agent_id=aid,
                                 allowed_tools=allowed, write_scope=task["write_scope"],
                                 coordinator_id=task["coordinator_id"])
        available.update({s["function"]["name"]: s for s in get_openai_tools(["swarm_board"])})
        tool_schemas = [available[name] for name in dict.fromkeys([*task["tools"], "swarm_board"])]
        prompt = build_system_prompt(base, session_id=session_id)
        prompt += (
            f"\n\n## Assigned swarm task\nYou are {task['agent_name']} ({aid}). "
            f"{task['instructions']}\nPurpose: {task['brief']}\n"
            "Work only on this task. Publish useful intermediate findings with swarm_board. "
            f"Coordinator: {task['coordinator_id']}. Use swarm_board(action=ask) when blocked "
            "or needing a decision; it waits for a correlated coordinator reply. "
            "Board updates arrive automatically between rounds. Share evidence, not assumptions. "
            f"Assigned tools: {', '.join(task['tools']) or 'none'}. "
            "File-edit tools are limited to the assigned write scope. "
            "Do not claim another worker's actions as yours."
        )
        if task.get("context_summary"):
            prompt += f"\nYour prior context in this chat: {task['context_summary']}"
        message = (
            f"User request: {request}\n\nYour task: {task['brief']}\n\n"
            f"Dependencies: {json.dumps(dependencies, ensure_ascii=False)}\n"
            f"File-edit tool scope: {json.dumps(task['write_scope'])}"
        )
        async for raw in run_turn(message, history=None, agent_id=base,
                                  session_id=session_id, system_prompt=prompt,
                                  tools=tool_schemas):
            ev = dict(raw)
            if ev.get("kind") == "swarm_event":
                # Durable board events are relayed once by the scheduler.
                continue
            ev["agent_id"] = aid
            ev["agent_name"] = task["agent_name"]
            ev["subagent"] = True
            ev["delegate_call_id"] = tid
            if ev.get("kind") == "final":
                result = ev.get("content") or ""
                ev["kind"] = "subagent_final"
            elif ev.get("kind") == "error":
                result = f"Error: {ev.get('message') or 'worker failed'}"
                status = "failed"
                ev["kind"] = "subagent_error"
            await events.put(("worker_event", task, ev))
    except asyncio.CancelledError:
        store.with_db(lambda conn: db.update_task(conn, tid, "cancelled"))
        raise
    except Exception as exc:
        log.exception("swarm worker failed run=%s task=%s", run_id, tid)
        result = f"Error: {exc}"
        status = "failed"
    finally:
        if token is not None:
            swarm_board.reset(token)
        reset_depth(depth_token)
    if not result:
        result, status = "Worker returned no result", "failed"
    store.with_db(lambda conn: db.update_task(conn, tid, status, result))
    if task["dynamic"]:
        store.with_db(lambda conn: db.update_agent_context(conn, aid, result))
    await events.put(("task_done", task, {"status": status, "result": result}))


async def _supervise(*, run_id: str, session_id: str, coordinator_id: str,
                     request: str, tasks: dict[str, dict[str, Any]],
                     guidance: list[dict[str, Any]], events: asyncio.Queue,
                     conversation: list[dict[str, Any]], after_id: int = 0) -> None:
    """One coalesced main-agent review, concurrently with the workers."""
    board = store.with_db(lambda c: db.list_events(c, run_id))
    snapshot_cursor = board[-1]["id"] if board else after_id
    token = swarm_board.bind(run_id=run_id, task_id="", agent_id=coordinator_id,
                             coordinator_id=coordinator_id, allowed_tools={"swarm_board"}, cursor=snapshot_cursor)
    depth_token = bind_depth(current_depth() + 1)
    try:
        snapshot = {
            "request": request, "user_updates": guidance,
            "tasks": [{**{k: t[k] for k in ("id", "key", "agent_id", "agent_name", "status")},
                       "brief": t["brief"][:2000], "result": str(t["result"])[:2000]}
                      for t in tasks.values()],
            "board": [{"event_id": e["id"], "task_id": e["task_id"], "kind": e["kind"], **e["payload"]}
                      for e in board if e["id"] > after_id and e["kind"] in {"finding", "message", "question"}][-40:],
        }
        prompt = build_system_prompt(coordinator_id, session_id=session_id) + (
            "\n\n## Active swarm coordinator\nYou own the user's goal while workers execute. "
            "Review shared evidence, reconcile conflicts, answer worker questions, and send specific "
            "corrections or useful findings to the relevant live task. User updates are instructions "
            "to you; translate them into guidance rather than blindly broadcasting them. "
            "Use only swarm_board. For every unanswered question from a live worker, send an answer "
            "to its agent_id AND task_id, with reply_to_event_id equal to the question event_id. "
            "If evidence is insufficient, say what the worker should check or report as blocked. "
            "You can ask another worker for evidence via send. Publish shared conclusions when useful. "
            "Treat board reports as unverified evidence, and do not widen task write scopes or "
            "claim external actions. Avoid repeating guidance already sent or messaging finished tasks. "
            "Publish only new, actionable conclusions through swarm_board. If there is nothing to act on, "
            "finish quietly; do not publish or send waiting, empty-board, unchanged-progress, or no-action updates. "
            "Expected final output: exactly NO_UPDATE if you made no new coordination action; "
            "exactly COORDINATED after successfully publishing a new conclusion or sending useful guidance. "
            "Do not append a summary, explanation, or progress report to either output. "
            "Both outputs are internal controls, never board posts or user-facing answers. "
            "Put substantive conclusions in publish and targeted instructions or replies in send. "
            "Your final text is internal bookkeeping and is not displayed to the user."
        )
        async for ev in run_turn(json.dumps(snapshot, ensure_ascii=False), history=None,
                                 agent_id=coordinator_id, session_id=session_id,
                                 system_prompt=prompt, tools=get_openai_tools(["swarm_board"]),
                                 max_iterations=4, conversation=conversation):
            if ev.get("kind") == "final" and not ev.get("continued"):
                await events.put(("coordinator_review_done", None, {"agent_id": coordinator_id,
                                                                "metrics": ev.get("metrics") or {}}))
            elif ev.get("kind") == "error":
                await events.put(("coordinator_error", None, {"content": ev.get("message") or "Review failed"}))
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("swarm coordinator review failed run=%s", run_id)
        await events.put(("coordinator_error", None, {"content": "Coordinator review failed; workers continue."}))
    finally:
        delivered_cursor = swarm_board.checkpoint()
        swarm_board.reset(token)
        reset_depth(depth_token)
        await events.put(("coordinator_finished", None, {"after_id": delivered_cursor}))


async def run_swarm_turn(
    request: str, *, session_id: str, coordinator_id: str,
    history: list[dict[str, Any]] | None, origin: str | None = None,
    initial_plan: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    from contextlib import aclosing
    from app.runtime.policy import resolve_turn
    from app.runtime.access import execution_scope, AccessDenied
    from app.runtime.supervision import admitted_turn

    try:
        context = resolve_turn(session_id, coordinator_id)
        with execution_scope(context):
            async with admitted_turn(context):
                async with aclosing(_run_swarm_owned(request, session_id=session_id,
                    coordinator_id=coordinator_id, history=history, origin=origin,
                    initial_plan=initial_plan)) as source:
                    async for event in source:
                        yield event
    except (AccessDenied, TimeoutError) as exc:
        yield {"kind": "error", "message": str(exc) or "Swarm duration limit reached"}


async def _run_swarm_owned(
    request: str, *, session_id: str, coordinator_id: str,
    history: list[dict[str, Any]] | None, origin: str | None = None,
    initial_plan: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Execute the main chat model's submitted plan and return worker results."""
    run_id = store.with_db(lambda conn: db.create_run(conn, session_id, request))
    tasks: dict[str, dict[str, Any]] = {}
    active: dict[str, asyncio.Task] = {}
    events: asyncio.Queue = asyncio.Queue()
    finished = False
    final_status = "done"
    board_cursor = 0
    supervisor: asyncio.Task | None = None
    review_pending = False
    guidance: list[dict[str, Any]] = []
    review_conversation: list[dict[str, Any]] = []
    review_cursor = 0
    review_guidance_count = 0
    last_review = asyncio.get_running_loop().time()
    coordinator = store.get_agent(coordinator_id) or {}
    yield _event(run_id, "run_started", {
        "request": request, "coordinator_id": coordinator_id,
        "coordinator_name": coordinator.get("name") or coordinator_id,
    })
    try:
        errors: list[str] = []
        accepted = _accept_plan(initial_plan or {}, session_id=session_id, run_id=run_id,
                                coordinator_id=coordinator_id, tasks=tasks, errors=errors)
        if not accepted:
            message = "Error: invalid swarm plan: " + "; ".join(errors)
            store.with_db(lambda conn: db.update_run(conn, run_id, "failed", message))
            finished = True
            yield {"kind": "swarm_result", "result": message, "error": True}
            yield _event(run_id, "run_done", {"status": "failed"})
            return
        for task in accepted:
            task["coordinator_id"] = coordinator_id
            tasks[task["key"]] = task
            yield _event(run_id, "task_created", {
                "task_id": task["id"], "key": task["key"], "agent_id": task["agent_id"],
                "agent_name": task["agent_name"], "brief": task["brief"],
                "depends_on": task["depends_on"], "tools": task["tools"],
                "purpose": task["purpose"], "dynamic": task["dynamic"],
            }, task["id"])
        yield _event(run_id, "phase", {"phase": "running"})
        while True:
            # The scheduler alone consumes composer steers. Workers and review
            # turns are nested and cannot steal user guidance from the main.
            async for steer in _emit_drained_steers(guidance, session_id):
                review_pending = True
                yield steer
                yield _event(run_id, "user_update", {"content": steer.get("content") or ""})
            posted = store.with_db(lambda conn: db.list_events(conn, run_id, board_cursor))
            for post in posted:
                board_cursor = max(board_cursor, post["id"])
                if post["kind"] in {"finding", "message", "question", "message_received", "question_resolved"}:
                    yield {"kind": "swarm_event", "run_id": run_id, "event_id": post["id"],
                           "event": post["kind"], "task_id": post["task_id"], **post["payload"]}
                    if (post["task_id"] and post["kind"] in {"finding", "question", "message"}
                            and post["payload"].get("to_agent_id") in {None, "", coordinator_id}):
                        review_pending = True
            # Ready work starts as soon as its dependencies succeed and write
            # scopes do not overlap another active task.
            for key, task in tasks.items():
                if len(active) >= MAX_ACTIVE:
                    break
                if task["status"] != "queued":
                    continue
                if not all(tasks[d]["status"] == "done" for d in task["depends_on"]):
                    continue
                if any(_overlap(task["write_scope"], tasks[k]["write_scope"])
                       for k in active):
                    continue
                task["status"] = "running"
                deps = {d: tasks[d]["result"] for d in task["depends_on"]}
                active[key] = asyncio.create_task(_worker(
                    task=task, request=request, session_id=session_id,
                    run_id=run_id, dependencies=deps, events=events,
                ))
            now = asyncio.get_running_loop().time()
            if active and supervisor is None and (review_pending or now - last_review >= REVIEW_INTERVAL):
                review_pending = False
                last_review = now
                yield _event(run_id, "coordinator_review", {"agent_id": coordinator_id, "content": "Reviewing worker progress"})
                supervisor = asyncio.create_task(_supervise(
                    run_id=run_id, session_id=session_id, coordinator_id=coordinator_id,
                    request=request, tasks=tasks, guidance=list(guidance[review_guidance_count:]), events=events,
                    conversation=review_conversation, after_id=review_cursor,
                ))
                review_guidance_count = len(guidance)
                review_cursor = board_cursor
            if not active and supervisor is None:
                if not any(t["status"] == "queued" for t in tasks.values()):
                    break
                for task in tasks.values():
                    if task["status"] == "queued":
                        task["status"] = "blocked"
                        store.with_db(lambda conn, t=task: db.update_task(conn, t["id"], "blocked", "Dependency failed"))
                        yield _event(run_id, "task_blocked", {"task_id": task["id"], "reason": "Dependency failed"}, task["id"])
                break
            try:
                kind, task, payload = await asyncio.wait_for(events.get(), timeout=0.2)
            except TimeoutError:
                continue
            if kind == "coordinator_finished":
                review_cursor = max(review_cursor, payload.get("after_id", 0))
                if supervisor is not None:
                    await supervisor
                supervisor = None
                last_review = asyncio.get_running_loop().time()
                # Updates delivered during a review are already in its retained
                # conversation. Only unseen evidence or new user guidance needs
                # another review, avoiding repeated calls for the same burst.
                unseen = store.with_db(lambda c: db.list_events(c, run_id, review_cursor))
                review_pending = len(guidance) > review_guidance_count or any(
                    e["task_id"] and e["kind"] in {"finding", "question", "message", "task_done"}
                    and e["payload"].get("to_agent_id") in {None, "", coordinator_id}
                    for e in unseen
                )
                continue
            if kind in {"coordinator_review_done", "coordinator_error"}:
                yield _event(run_id, kind, payload)
                continue
            if kind == "task_started":
                yield _event(run_id, "task_started", {
                    "task_id": task["id"], "agent_id": task["agent_id"],
                    "agent_name": task["agent_name"], "brief": task["brief"],
                }, task["id"])
                yield {"kind": "subagent_start", "agent_id": task["agent_id"],
                       "agent_name": task["agent_name"], "task": task["brief"],
                       "from": coordinator_id, "delegate_call_id": task["id"]}
            elif kind == "worker_event":
                yield payload
            elif kind == "task_done":
                task["status"] = payload["status"]
                task["result"] = payload["result"]
                active.pop(task["key"], None)
                if active:
                    review_pending = True
                yield _event(run_id, "task_done", {
                    "task_id": task["id"], "agent_id": task["agent_id"],
                    "status": payload["status"], "content": payload["result"],
                }, task["id"])
                yield {"kind": "subagent_done", "agent_id": task["agent_id"],
                       "agent_name": task["agent_name"], "content": payload["result"],
                       "status": "ok" if payload["status"] == "done" else "error",
                       "delegate_call_id": task["id"]}
                continue
        final_status = "done" if all(t["status"] == "done" for t in tasks.values()) else "failed"
        result = json.dumps({
            "run_id": run_id, "request": request, "status": final_status,
            "tasks": [{"key": t["key"], "agent": t["agent_name"], "agent_id": t["agent_id"], "status": t["status"],
                       "result": str(t["result"])[:6000]} for t in tasks.values()],
            "findings": [{**e["payload"], "content": str(e["payload"].get("content") or "")[:2000]} for e in store.with_db(lambda conn: db.list_events(conn, run_id))
                         if e["kind"] in {"finding", "message", "question", "user_update"}][-40:],
            "user_updates": guidance,
        }, ensure_ascii=False)
        store.with_db(lambda conn: db.update_run(conn, run_id, final_status, result))
        finished = True
        yield _event(run_id, "phase", {"phase": "synthesizing"})
        yield {"kind": "swarm_result", "result": result, "error": final_status != "done"}
        yield _event(run_id, "run_done", {"status": final_status})
    finally:
        if supervisor is not None:
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
        for task in active.values():
            task.cancel()
        if active:
            await asyncio.gather(*active.values(), return_exceptions=True)
        if not finished:
            for task in tasks.values():
                if task["status"] in {"queued", "running"}:
                    store.with_db(lambda conn, t=task: db.update_task(
                        conn, t["id"], "cancelled", "Run stopped before completion"
                    ))
            store.with_db(lambda conn: db.update_run(conn, run_id, "cancelled"))
