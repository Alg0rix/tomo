"""Dynamic, opt-in orchestration for a single chat turn.

The model proposes workers and tasks; code owns identity, access, scheduling,
state transitions, cancellation, and the final handoff back to the coordinator.
Workers can be configured agents or session-local instances created on demand.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from typing import Any

from app.models.mixins import swarm as db
from app.runtime.agent.context import build_system_prompt
from app.runtime.agent.loop import run_turn
from app.runtime.llm import get_llm
from app.runtime.tools import swarm_board
from app.runtime.tools.registry import get_openai_tools
from app.services.store import store

log = logging.getLogger(__name__)
MAX_ACTIVE = 4
MAX_TASKS = 12
MAX_PLAN_CALLS = 4
_SKILL_PATH = Path(__file__).resolve().parents[3] / "skills" / "internal" / "swarm" / "SKILL.md"
# Worker creation belongs to the coordinator's bounded scheduler. Other
# capabilities are selected per task from the agent's enabled tool catalog.
_ORCHESTRATION_TOOLS = {"delegate", "create_agent"}


def _json(content: str | None) -> dict[str, Any]:
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


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


async def _plan(
    coordinator_id: str, request: str, session_id: str, run_id: str,
    tasks: dict[str, dict[str, Any]], active: set[str],
    planner: Any = None,
    advisory: bool = False,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    configured = [
        {"id": a["id"], "name": a["name"], "role": a.get("role") or "",
         "description": a.get("description") or "", "workplace_id": a.get("workplace_id") or "",
         "tools": [s.get("function", {}).get("name") for s in store.get_agent_openai_tools(a["id"])
                   if s.get("function", {}).get("name") not in _ORCHESTRATION_TOOLS]}
        for a in store.list_agents() if a.get("enabled")
    ]
    local = store.with_db(lambda conn: db.list_agents(conn, session_id))
    board = [
        {"key": key, "agent_id": t["agent_id"], "status": t["status"],
         "brief": t["brief"], "tools": t.get("tools") or [],
         "result": str(t.get("result") or "")[:3000]}
        for key, t in tasks.items()
    ]
    instruction = (
        _SKILL_PATH.read_text(encoding="utf-8") + "\n\n"
        + ("Decide whether the user explicitly asked for a team (decision=run), "
         "the task would benefit from a team but needs approval (decision=propose), "
         "or a single agent should answer (decision=solo). Return empty tasks for solo. "
         "For propose, require at least two genuinely useful independent tasks. "
         if advisory else "You coordinate a user-requested agent swarm. ")
        + "Return one JSON object only. For decision=run, consent_quote must be an exact "
        "substring of the user's request that explicitly asks for multiple agents or a team. "
        "Do not use run for a request that merely mentions agents as subject matter. "
        'Shape: {"decision":"solo|propose|run","consent_quote":"...","agents":[{"name":"...","purpose":"...","instructions":"...",'
        '"base_agent_id":"..."}],"tasks":[{"key":"unique",'
        '"agent_id":"configured id OR session agent id OR new agent name",'
        '"brief":"precise objective, output format and boundary",'
        '"depends_on":["task key"],"write_scope":["relative/path"],'
        '"tools":["enabled tool name"]}],'
        '"messages":[{"to_agent_id":"id","content":"steering"}]}. '
        "Choose a configured agent directly when the user named it or its tools, model, "
        "memory, or workplace help. Create session-only agents for missing specialties, "
        "independent perspectives, or multiple copies of a role. Mix both if useful. "
        "The coordinator is listed as a template but cannot be assigned a worker task. "
        "Do not duplicate work. Independent tasks should run concurrently. Dependent tasks "
        "must name prerequisites. Choose the few enabled tools each task needs, including "
        "bash or portal when appropriate. Tool use still follows the user's permission settings. "
        "write_scope applies to file-edit tools; give relative paths for those tools. "
        "Use messages to steer running workers. "
        "Return empty tasks when work is complete or one agent suffices. "
        "Never invent an agent id outside the listed roster or new names."
    )
    payload = {
        "request": request, "configured_agents": configured,
        "recent_chat": [
            {"role": entry.get("type"), "content": str(entry.get("content") or "")[:3000]}
            for entry in (history or [])[-10:]
            if entry.get("type") in {"user", "final"}
        ],
        "session_agents": [{"id": a["id"], "name": a["name"], "purpose": a["purpose"]} for a in local],
        "tasks": board, "active_task_keys": sorted(active),
        "findings": [e["payload"] for e in store.with_db(
            lambda conn: db.list_events(conn, run_id)
        ) if e["kind"] in {"finding", "message"}][-20:] if run_id else [],
        "remaining_task_budget": MAX_TASKS - len(tasks),
    }
    try:
        model = planner or get_llm(coordinator_id)
        response = await model.complete(
            [{"role": "system", "content": instruction},
             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], []
        )
        return _json(response.content)
    except Exception:
        log.exception("swarm planner failed")
        return {}


async def advise_swarm(request: str, *, session_id: str, coordinator_id: str,
                       planner: Any = None,
                       history: list[dict[str, Any]] | None = None) -> tuple[str, dict[str, Any], str] | None:
    """Read the internal skill and draft a proposal, without starting workers."""
    if not request.strip():
        return None
    plan = await _plan(coordinator_id, request, session_id, "", {}, set(),
                       planner=planner, advisory=True, history=history)
    decision = str(plan.get("decision") or "solo")
    if decision not in {"run", "propose"}:
        return None
    # Ground immediate execution in the user's own words. Otherwise even a
    # model-produced "run" remains only a proposal.
    quote = str(plan.get("consent_quote") or "").strip()
    if decision == "run" and (not quote or quote.casefold() not in request.casefold()):
        decision = "propose"
    proposed = plan.get("tasks")
    if not isinstance(proposed, list) or len(proposed) < (1 if decision == "run" else 2):
        return None
    names = {a.get("name") for a in plan.get("agents", []) if isinstance(a, dict)}
    known = {a["id"] for a in store.list_agents() if a.get("enabled")}
    known.update(a["id"] for a in store.with_db(lambda conn: db.list_agents(conn, session_id)))
    if any(not isinstance(t, dict) or t.get("agent_id") not in known | names
           or not str(t.get("brief") or "").strip() for t in proposed):
        return None
    lines = ["Aku bisa bagi ini ke beberapa agent:"]
    for task in proposed[:4]:
        lines.append(f"- {task['agent_id']}: {str(task['brief']).strip()[:180]}")
    lines.append("Mau saya jalankan rencana ini? Balas **gas** atau **ya**. Kalau tidak, saya kerjakan sendiri.")
    return decision, plan, "\n".join(lines)


def _accept_plan(
    plan: dict[str, Any], *, session_id: str, run_id: str,
    coordinator_id: str, tasks: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate proposed agents/tasks and persist only safe, new work."""
    raw_agents = plan.get("agents") or []
    raw_tasks = plan.get("tasks") or []
    if not isinstance(raw_agents, list) or len(raw_agents) > MAX_ACTIVE:
        return []
    if not isinstance(raw_tasks, list) or not raw_tasks or len(raw_tasks) > MAX_TASKS - len(tasks):
        return []
    # Validate the dependency graph before creating session-local agents.
    raw_keys = [str(t.get("key") or "").strip()[:80] for t in raw_tasks if isinstance(t, dict)]
    if len(raw_keys) != len(raw_tasks) or len(set(raw_keys)) != len(raw_keys) or not all(raw_keys):
        return []
    valid_keys = set(tasks) | set(raw_keys)
    if any(
        not isinstance(t.get("depends_on") or [], list)
        or any(not isinstance(d, str) or d not in valid_keys or d == t["key"]
               for d in (t.get("depends_on") or []))
        or not _scope_valid(t.get("write_scope") or [])
        for t in raw_tasks
    ):
        return []
    unresolved_keys = {
        key: set(raw.get("depends_on") or []) - set(tasks)
        for key, raw in zip(raw_keys, raw_tasks)
    }
    resolved_keys = set(tasks)
    while unresolved_keys:
        ready_keys = {key for key, deps in unresolved_keys.items() if deps <= resolved_keys}
        if not ready_keys:
            return []
        resolved_keys.update(ready_keys)
        for key in ready_keys:
            unresolved_keys.pop(key)
    configured = {a["id"]: a for a in store.list_agents() if a.get("enabled")}
    local = {a["id"]: a for a in store.with_db(lambda conn: db.list_agents(conn, session_id))}
    names = {a["name"]: a["id"] for a in local.values()}
    candidate_names = set(names)
    new_agent_bases: dict[str, str] = {}
    for raw in raw_agents:
        if not isinstance(raw, dict):
            return []
        name = str(raw.get("name") or "").strip()[:80]
        purpose = str(raw.get("purpose") or "").strip()
        base = str(raw.get("base_agent_id") or coordinator_id).strip()
        if not name or not purpose or base not in configured:
            return []
        candidate_names.add(name)
        new_agent_bases[name] = base
    enabled_tool_cache: dict[str, set[str]] = {}

    def enabled_tools(base_agent_id: str) -> set[str]:
        if base_agent_id not in enabled_tool_cache:
            enabled_tool_cache[base_agent_id] = {
                s.get("function", {}).get("name")
                for s in store.get_agent_openai_tools(base_agent_id)
                if s.get("function", {}).get("name") not in _ORCHESTRATION_TOOLS
            }
        return enabled_tool_cache[base_agent_id]

    for raw in raw_tasks:
        aid = str(raw.get("agent_id") or "").strip()
        if (not str(raw.get("brief") or "").strip() or aid == coordinator_id
                or aid not in configured and aid not in local and aid not in candidate_names):
            return []
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
            return []
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
    try:
        store.with_db(lambda conn: db.update_task(conn, tid, "running"))
        await events.put(("task_started", task, {}))
        available = {
            s.get("function", {}).get("name"): s
            for s in store.get_agent_openai_tools(base)
        }
        selected = set(task["tools"])
        missing = selected - available.keys()
        if missing:
            raise RuntimeError(f"Assigned tools are no longer available: {', '.join(sorted(missing))}")
        allowed = selected | {"swarm_board"}
        tool_schemas = [available[name] for name in task["tools"]]
        tool_schemas += get_openai_tools(["swarm_board"])
        prompt = build_system_prompt(base, session_id=session_id)
        prompt += (
            f"\n\n## Assigned swarm task\nYou are {task['agent_name']} ({aid}). "
            f"{task['instructions']}\nPurpose: {task['brief']}\n"
            "Work only on this task. Publish useful intermediate findings with swarm_board. "
            "Read the board before finishing for messages from the coordinator. "
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
        token = swarm_board.bind(run_id=run_id, task_id=tid, agent_id=aid,
                                 allowed_tools=allowed, write_scope=task["write_scope"])
        async for raw in run_turn(message, history=None, agent_id=base,
                                  session_id=session_id, system_prompt=prompt,
                                  tools=tool_schemas):
            ev = dict(raw)
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
    if not result:
        result, status = "Worker returned no result", "failed"
    store.with_db(lambda conn: db.update_task(conn, tid, status, result))
    if task["dynamic"]:
        store.with_db(lambda conn: db.update_agent_context(conn, aid, result))
    await events.put(("task_done", task, {"status": status, "result": result}))


async def run_swarm_turn(
    request: str, *, session_id: str, coordinator_id: str,
    history: list[dict[str, Any]] | None, origin: str | None = None,
    planner: Any = None,
    initial_plan: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Plan, schedule, revise and synthesize a user-authorized swarm turn."""
    run_id = store.with_db(lambda conn: db.create_run(conn, session_id, request))
    tasks: dict[str, dict[str, Any]] = {}
    active: dict[str, asyncio.Task] = {}
    events: asyncio.Queue = asyncio.Queue()
    plan_calls = 0
    need_plan = True
    finished = False
    final_status = "done"
    yield _event(run_id, "run_started", {"request": request})
    try:
        while True:
            if need_plan and plan_calls < MAX_PLAN_CALLS and len(tasks) < MAX_TASKS:
                plan_calls += 1
                need_plan = False
                if initial_plan is not None:
                    proposal, initial_plan = initial_plan, None
                else:
                    proposal = await _plan(coordinator_id, request, session_id, run_id,
                                           tasks, set(active), planner=planner,
                                           history=history)
                accepted = _accept_plan(proposal, session_id=session_id, run_id=run_id,
                                        coordinator_id=coordinator_id, tasks=tasks)
                for task in accepted:
                    tasks[task["key"]] = task
                    yield _event(run_id, "task_created", {
                        "task_id": task["id"], "key": task["key"],
                        "agent_id": task["agent_id"], "agent_name": task["agent_name"],
                        "brief": task["brief"], "depends_on": task["depends_on"],
                        "tools": task["tools"],
                        "dynamic": task["dynamic"],
                    }, task["id"])
                for msg in proposal.get("messages") or []:
                    if isinstance(msg, dict) and msg.get("to_agent_id") and msg.get("content"):
                        yield _event(run_id, "message", {
                            "agent_id": coordinator_id, "to_agent_id": str(msg["to_agent_id"]),
                            "content": str(msg["content"])[:4000],
                        })
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
            if not active:
                if not any(t["status"] == "queued" for t in tasks.values()):
                    break
                for task in tasks.values():
                    if task["status"] == "queued":
                        task["status"] = "blocked"
                        store.with_db(lambda conn, t=task: db.update_task(conn, t["id"], "blocked", "Dependency failed"))
                        yield _event(run_id, "task_blocked", {"task_id": task["id"], "reason": "Dependency failed"}, task["id"])
                break
            kind, task, payload = await events.get()
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
                if payload.get("kind") == "tool_result" and payload.get("tool") == "swarm_board":
                    yield _event(run_id, "board_updated", {"agent_id": task["agent_id"]}, task["id"])
                    need_plan = True
            elif kind == "task_done":
                task["status"] = payload["status"]
                task["result"] = payload["result"]
                active.pop(task["key"], None)
                yield _event(run_id, "task_done", {
                    "task_id": task["id"], "agent_id": task["agent_id"],
                    "status": payload["status"], "content": payload["result"],
                }, task["id"])
                yield {"kind": "subagent_done", "agent_id": task["agent_id"],
                       "agent_name": task["agent_name"], "content": payload["result"],
                       "status": "ok" if payload["status"] == "done" else "error",
                       "delegate_call_id": task["id"]}
                # Replan while siblings still work; don't wait for a batch barrier.
                need_plan = True
                continue
        if not tasks:
            # Even explicit opt-in need not spawn anyone for a trivial request.
            coordinator_tools = [s for s in store.get_agent_openai_tools(coordinator_id)
                                 if s.get("function", {}).get("name") not in {"delegate", "create_agent"}]
            async for ev in run_turn(None, history=history, agent_id=coordinator_id,
                                     session_id=session_id, origin=origin,
                                     tools=coordinator_tools):
                if ev.get("kind") == "final":
                    store.with_db(lambda conn: db.update_run(conn, run_id, "done", ev.get("content") or ""))
                elif ev.get("kind") == "error":
                    final_status = "failed"
                yield ev
        else:
            board = [
                {"task": t["brief"], "agent": t["agent_name"],
                 "status": t["status"], "result": str(t["result"])[:6000]}
                for t in tasks.values()
            ]
            findings = [
                {**event["payload"], "content": str(event["payload"].get("content") or "")[:2000]}
                for event in store.with_db(
                    lambda conn: db.list_events(conn, run_id)
                ) if event["kind"] in {"finding", "message"}
            ][-40:]
            synthesis = (
                "Synthesize the completed multi-agent work for the user's request. "
                "Check each result against the request. State failed or blocked work honestly. "
                "Do not launch more workers.\n\n"
                f"Original request: {request}\n\nTask board:\n{json.dumps(board, ensure_ascii=False)}"
                f"\n\nShared findings and messages:\n{json.dumps(findings, ensure_ascii=False)}"
            )
            coordinator_tools = [s for s in store.get_agent_openai_tools(coordinator_id)
                                 if s.get("function", {}).get("name") not in {"delegate", "create_agent"}]
            async for ev in run_turn(synthesis, history=history, agent_id=coordinator_id,
                                     session_id=session_id, origin=origin,
                                     tools=coordinator_tools):
                if ev.get("kind") == "final":
                    store.with_db(lambda conn: db.update_run(conn, run_id, "done", ev.get("content") or ""))
                elif ev.get("kind") == "error":
                    final_status = "failed"
                yield ev
        if final_status != "done":
            store.with_db(lambda conn: db.update_run(conn, run_id, final_status))
        finished = True
        yield _event(run_id, "run_done", {"status": final_status})
    finally:
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
