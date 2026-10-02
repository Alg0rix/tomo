"""Main-chat swarm planning, worker execution, and delegation regression tests."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import pytest

from app.models.mixins import swarm as swarm_store
from app.models.schema import migrate
from app.runtime.coordinator import swarm
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.tools import swarm_board
from app.services.chat import run_session_turn
from app.services.store import store


@pytest.mark.parametrize("execution_mode", [None, "swarm"])
@pytest.mark.parametrize("bad_arguments, expected_error", [
    ({}, "plan workers in this main chat"),
    ({"plan": {"tasks": [{"key": "bad", "agent_id": "main", "brief": "Check"}]}}, "coordinator itself"),
    ({"plan": {"tasks": [{"key": "bad", "agent_id": "research", "brief": "Check", "tools": ["not-real"]}]}}, "enabled catalog"),
])
async def test_plan_repair_workers_and_synthesis_use_one_main_chat_loop(tmp_path, monkeypatch, execution_mode, bad_arguments, expected_error):
    from tests.fakes.llm import ScriptedLLM, text_reply

    store.rebind(tmp_path / "main_chat_plan.db")
    sid = store.get_or_create_session("main", "web")
    request = "Test swarm lagi"
    plan = {"tasks": [{"key": "check", "agent_id": "research", "brief": "Report a test finding", "tools": []}]}
    root_rounds = []
    factories = []

    class MainModel(ScriptedLLM):
        async def complete(self, messages, tools=None):
            root_rounds.append(messages[:])
            assert any(s["function"]["name"] == "start_swarm" for s in tools)
            if self.remaining == 2:
                feedback = [m["content"] for m in messages if m["role"] == "tool"]
                assert any(expected_error in m for m in feedback)
                assert store.with_db(lambda c: swarm_store.list_runs(c, sid)) == []
            if self.remaining == 1:
                result = json.loads([m["content"] for m in messages if m["role"] == "tool"][-1])
                assert result["tasks"][0]["result"] == "Worker checked dispatch"
            return await super().complete(messages, tools)

    main = MainModel([
        LLMResponse(content=None, tool_calls=[ToolCall(id="missing", name="start_swarm", arguments={"request": request, **bad_arguments})]),
        LLMResponse(content=None, tool_calls=[ToolCall(id="valid", name="start_swarm", arguments={"request": request, "plan": plan})]),
        text_reply("Dispatch verified from worker evidence"),
    ])
    worker = ScriptedLLM([text_reply("Worker checked dispatch")])

    def get_model(agent_id, **kwargs):
        factories.append((agent_id, kwargs.get("reasoning_effort")))
        return worker if agent_id == "research" else main

    monkeypatch.setattr("app.runtime.agent.loop.get_llm", get_model)
    monkeypatch.setattr(store, "resolve_session_reasoning_effort", lambda *args: "max")
    from app.channels.web import stream_turn_sse
    chunks = [c async for c in stream_turn_sse(sid, "main", request, 0, execution_mode=execution_mode or "solo")]
    assert factories == [("main", "max"), ("research", "max")]
    assert len(root_rounds) == 3
    assert root_rounds[0][0] == root_rounds[-1][0]
    assert any(chunk.startswith("event: swarm.event\n") and '"task_started"' in chunk for chunk in chunks)
    history = store.get_session_history(sid)
    assert [e["content"] for e in history if e["type"] == "final"] == ["Dispatch verified from worker evidence"]
    assert main.remaining == worker.remaining == 0


async def test_single_agent_chat_can_delegate_repeatedly_without_mentions(tmp_path, monkeypatch) -> None:
    from tests.fakes.llm import ScriptedLLM, text_reply

    store.rebind(tmp_path / "single_chat_delegate.db")
    sid = store.get_or_create_session("main", "web")
    store.update_agent("research", {"enabled": False})

    class MainLLM(ScriptedLLM):
        async def complete(self, messages, tools=None):
            assert "delegate" in {s["function"]["name"] for s in tools}
            return await super().complete(messages, tools)

    def delegate_call(task):
        return LLMResponse(content=None, tool_calls=[ToolCall(
            id=task, name="delegate", arguments={"agent_id": "research", "reason": task},
        )])

    main = MainLLM([
        text_reply("Hello"),
        delegate_call("Check the supplied research sources"),
        text_reply("First delegated result"),
        delegate_call("Check the remaining research gap"),
        text_reply("Second delegated result"),
    ])
    research = ScriptedLLM([text_reply("Sources checked"), text_reply("Gap checked")])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm",
                        lambda agent_id, **kwargs: research if agent_id == "research" else main)
    async for _ in run_session_turn(sid, "Hello", "web", start_seq=0):
        pass
    # An agent enabled after the chat was created is routable next turn.
    store.update_agent("research", {"enabled": True})
    chunks = []
    for request in ("Delegate source checking to Research", "Delegate the remaining gap to Research"):
        chunks.extend([chunk async for chunk in run_session_turn(sid, request, "web", start_seq=0)])
    assert sum(chunk.startswith("event: delegate\n") for chunk in chunks) == 2
    history = store.get_session_history(sid)
    calls = [e for e in history if e["type"] == "tool_call" and e["function"] == "delegate"]
    assert len(calls) == 2
    outputs = [e for e in history if e["type"] == "tool_output" and not e.get("error")]
    assert any("Sources checked" in e["content"] for e in outputs)
    assert any("Gap checked" in e["content"] for e in outputs)
    assert store.get_session(sid)["agent_ids"] == ["main"]
    assert store.with_db(lambda conn: swarm_store.list_runs(conn, sid)) == []
    assert main.remaining == research.remaining == 0


async def test_dynamic_workers_run_concurrently_then_synthesize(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "dynamic_swarm.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    plan = {
        "agents": [
            {"name": "Reviewer A", "purpose": "Find correctness issues", "base_agent_id": "main"},
            {"name": "Reviewer B", "purpose": "Find usability issues", "base_agent_id": "main"},
        ],
        "tasks": [
            {"key": "correctness", "agent_id": "Reviewer A", "brief": "Check correctness"},
            {"key": "usability", "agent_id": "Reviewer B", "brief": "Check usability"},
        ],
    }

    active = 0
    peak = 0

    async def fake_turn(message, *, agent_id, **kwargs):
        nonlocal active, peak
        if "Assigned swarm task" in kwargs.get("system_prompt", ""):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            yield {"kind": "final", "content": f"Finding from {message}"}
        else:
            yield {"kind": "final", "content": "Combined answer"}

    monkeypatch.setattr(swarm, "run_turn", fake_turn)

    events = [ev async for ev in swarm.run_swarm_turn(
        "Review this", session_id=session_id, coordinator_id="main", history=[], initial_plan=plan
    )]
    assert peak == 2
    assert len([e for e in events if e.get("event") == "task_created"]) == 2
    assert len([e for e in events if e.get("event") == "task_done"]) == 2
    assert events[-1]["event"] == "run_done"
    assert store.get_session(session_id)["agent_ids"] == ["main"]
    runs = store.with_db(lambda conn: swarm_store.list_runs(conn, session_id))
    assert runs[0]["status"] == "done"
    assert len(json.loads(runs[0]["result"])["tasks"]) == 2
    workers = store.with_db(lambda conn: swarm_store.list_agents(conn, session_id))
    assert {a["name"] for a in workers} == {"Reviewer A", "Reviewer B"}


async def test_clarify_answer_can_dispatch_without_another_chat_message(tmp_path, monkeypatch) -> None:
    from app.runtime.permissions import hitl
    from tests.fakes.llm import ScriptedLLM, text_reply

    store.rebind(tmp_path / "clarify_dispatch.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    request = "Review the supplied evidence"

    class MainLLM(ScriptedLLM):
        async def complete(self, messages, tools=None):
            if self.remaining == 2:
                answers = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
                assert any(a.get("user_response") == "Evidence and coverage" for a in answers)
            return await super().complete(messages, tools)

    main = MainLLM([
        LLMResponse(content=None, tool_calls=[ToolCall(
            id="clarify", name="clarify", arguments={"question": "Which review criteria?",
                                                      "choices": ["Evidence", "Evidence and coverage"]},
        )]),
        LLMResponse(content=None, tool_calls=[ToolCall(
            id="swarm", name="start_swarm",
            arguments={"request": request + "; clarify answer: Evidence and coverage",
                       "plan": {"tasks": [{"key": "review", "agent_id": "research", "brief": "Review evidence", "tools": []}]}},
        )]),
        text_reply("Reviewed evidence and coverage"),
    ])
    worker = ScriptedLLM([text_reply("Evidence finding")])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm",
                        lambda agent_id, **kwargs: worker if agent_id == "research" else main)
    answered = False
    async for chunk in run_session_turn(sid, request, "web", start_seq=0):
        for line in chunk.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:])
                if chunk.startswith("event: clarify_required\n"):
                    hitl.resolve_clarify(payload["id"], "Evidence and coverage")
                    answered = True
    assert answered
    runs = store.with_db(lambda conn: swarm_store.list_runs(conn, sid))
    assert runs[0]["status"] == "done"
    assert "clarify answer: Evidence and coverage" in runs[0]["request"]
    assert json.loads(runs[0]["result"])["tasks"][0]["result"] == "Evidence finding"
    assert len([e for e in store.get_session_history(sid) if e["type"] == "user"]) == 1
    assert main.remaining == worker.remaining == 0


def test_invalid_plan_does_not_create_session_worker(tmp_path) -> None:
    store.rebind(tmp_path / "bad_plan.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    run_id = store.with_db(lambda conn: swarm_store.create_run(conn, session_id, "Review"))
    plan = {"agents": [{"name": "Temp", "purpose": "Review", "base_agent_id": "main"}],
            "tasks": [{"key": "a", "agent_id": "Temp", "brief": "Review",
                       "depends_on": ["missing"]}]}
    assert swarm._accept_plan(plan, session_id=session_id, run_id=run_id,
                              coordinator_id="main", tasks={}) == []
    assert store.with_db(lambda conn: swarm_store.list_agents(conn, session_id)) == []


async def test_dependent_worker_waits_for_result(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "dependencies.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    plan = {"agents": [], "tasks": [
        {"key": "first", "agent_id": "ops", "brief": "Find the cause"},
        {"key": "second", "agent_id": "research", "brief": "Verify the cause",
         "depends_on": ["first"]},
    ]}
    seen = []

    async def fake_turn(message, *, agent_id, **kwargs):
        if "Assigned swarm task" in kwargs.get("system_prompt", ""):
            seen.append(("start", agent_id, message))
            await asyncio.sleep(0)
            seen.append(("done", agent_id, message))
            yield {"kind": "final", "content": f"Result from {agent_id}"}
        else:
            yield {"kind": "final", "content": "Verified"}

    monkeypatch.setattr(swarm, "run_turn", fake_turn)
    [e async for e in swarm.run_swarm_turn(
        "Find and verify", session_id=session_id, coordinator_id="main",
        history=[], initial_plan=plan,
    )]
    assert [kind + ":" + agent for kind, agent, _ in seen] == [
        "start:ops", "done:ops", "start:research", "done:research",
    ]
    assert "Result from ops" in seen[2][2]


async def test_failed_worker_blocks_dependencies_and_returns_evidence(tmp_path, monkeypatch):
    store.rebind(tmp_path / "worker_failure.db")
    sid = store.get_or_create_session("main", "web")
    plan = {"tasks": [
        {"key": "first", "agent_id": "ops", "brief": "Inspect", "tools": []},
        {"key": "second", "agent_id": "research", "brief": "Verify", "depends_on": ["first"], "tools": []},
    ]}

    async def failing_worker(*args, agent_id, **kwargs):
        assert agent_id == "ops"
        yield {"kind": "error", "message": "Inspection unavailable"}

    monkeypatch.setattr(swarm, "run_turn", failing_worker)
    events = [e async for e in swarm.run_swarm_turn("Inspect", session_id=sid, coordinator_id="main", history=None, initial_plan=plan)]
    result = next(e for e in events if e.get("kind") == "swarm_result")
    assert result["error"]
    board = json.loads(result["result"])
    assert [t["status"] for t in board["tasks"]] == ["failed", "blocked"]
    assert "Inspection unavailable" in board["tasks"][0]["result"]
    assert events[-1]["status"] == "failed"


async def test_disconnect_cancels_swarm_workers(tmp_path, monkeypatch):
    store.rebind(tmp_path / "worker_cancel.db")
    sid = store.get_or_create_session("main", "web")
    cancelled = asyncio.Event()

    async def waiting_worker(*args, **kwargs):
        try:
            await asyncio.Event().wait()
            yield {"kind": "final", "content": "Never"}
        finally:
            cancelled.set()

    monkeypatch.setattr(swarm, "run_turn", waiting_worker)
    events = swarm.run_swarm_turn("Inspect", session_id=sid, coordinator_id="main", history=None,
                                  initial_plan={"tasks": [{"key": "wait", "agent_id": "ops", "brief": "Wait", "tools": []}]})
    async for event in events:
        if event.get("event") == "task_started":
            break
    await events.aclose()
    assert cancelled.is_set()
    runs = store.with_db(lambda c: swarm_store.list_runs(c, sid))
    assert runs[0]["status"] == "cancelled"
    tasks = store.with_db(lambda c: swarm_store.list_tasks(c, runs[0]["id"]))
    assert tasks[0]["status"] == "cancelled"


async def test_coordinator_selects_bash_and_portal_for_worker(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "selected_tools.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    plan = {"agents": [], "tasks": [
        {"key": "prod_a", "agent_id": "ops", "brief": "Inspect prod A",
         "tools": ["bash", "portal", "swarm_board"]},
    ]}
    seen = []

    async def fake_turn(message, *, agent_id, **kwargs):
        if "Assigned swarm task" in kwargs.get("system_prompt", ""):
            names = {s["function"]["name"] for s in kwargs["tools"]}
            assert len(kwargs["tools"]) == len(names)
            seen.append(names)
            assert names == {"bash", "portal", "swarm_board"}
            assert swarm_board.authorize("bash", {"command": "true"}) is None
            assert swarm_board.authorize("portal", {"action": "list"}) is None
            assert swarm_board.authorize("read_file", {"path": "x"}) is not None
            yield {"kind": "final", "content": "Inspected prod A"}
        else:
            yield {"kind": "final", "content": "Summary"}

    monkeypatch.setattr(swarm, "run_turn", fake_turn)
    [e async for e in swarm.run_swarm_turn(
        "Investigate prod A", session_id=session_id, coordinator_id="main",
        history=[], initial_plan=plan,
    )]
    assert seen == [{"bash", "portal", "swarm_board"}]
    run = store.with_db(lambda conn: swarm_store.list_runs(conn, session_id))[0]
    task = store.with_db(lambda conn: swarm_store.list_tasks(conn, run["id"]))[0]
    assert task["tools"] == ["bash", "portal", "swarm_board"]


async def test_board_posts_stream_live_with_run_phases(tmp_path, monkeypatch) -> None:
    """The work panel renders findings while workers run, then marks synthesis."""
    store.rebind(tmp_path / "board_live.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    plan = {"agents": [{"name": "Scout", "purpose": "Finds prices", "base_agent_id": "main"}],
            "tasks": [{"key": "price", "agent_id": "Scout", "brief": "Find GPU prices"}]}

    async def fake_turn(message, *, agent_id, **kwargs):
        if "Assigned swarm task" in kwargs.get("system_prompt", ""):
            swarm_board.run({"action": "publish", "content": "H100 is $2.49/hr"})
            yield {"kind": "tool_result", "tool": "swarm_board", "result": "Saved"}
            yield {"kind": "final", "content": "Prices collected"}
        else:
            yield {"kind": "final", "content": "Summary"}

    monkeypatch.setattr(swarm, "run_turn", fake_turn)
    events = [e async for e in swarm.run_swarm_turn(
        "Price GPUs", session_id=session_id, coordinator_id="main",
        history=[], initial_plan=plan,
    )]
    kinds = [e.get("event") for e in events if e.get("kind") == "swarm_event"]
    finding = next(e for e in events if e.get("event") == "finding")
    assert finding["content"] == "H100 is $2.49/hr" and finding["task_id"]
    assert kinds.index("finding") < kinds.index("task_done")
    assert kinds.index("phase") < kinds.index("task_started")
    phases = [e["phase"] for e in events if e.get("event") == "phase"]
    assert phases == ["running", "synthesizing"]
    created = next(e for e in events if e.get("event") == "task_created")
    assert created["purpose"] == "Finds prices" and created["dynamic"] is True
    run = store.with_db(lambda conn: swarm_store.list_runs(conn, session_id))[0]
    stored = [e["kind"] for e in store.with_db(lambda conn: swarm_store.list_events(conn, run["id"]))]
    assert stored.count("finding") == 1


def test_coordinator_cannot_assign_disabled_tool(tmp_path) -> None:
    store.rebind(tmp_path / "disabled_tool.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    store.set_agent_tools("ops", {"bash": False})
    run_id = store.with_db(lambda conn: swarm_store.create_run(conn, session_id, "Review"))
    plan = {"agents": [], "tasks": [
        {"key": "prod_a", "agent_id": "ops", "brief": "Inspect prod A",
         "tools": ["bash"]},
    ]}
    assert swarm._accept_plan(plan, session_id=session_id, run_id=run_id,
                              coordinator_id="main", tasks={}) == []


def test_existing_task_table_gains_tool_selection_column(tmp_path) -> None:
    conn = sqlite3.connect(tmp_path / "older_schema.db")
    conn.execute(
        "CREATE TABLE swarm_tasks (id TEXT PRIMARY KEY, run_id TEXT, agent_id TEXT, "
        "brief TEXT, depends_json TEXT, write_scope_json TEXT, status TEXT, result TEXT, "
        "created_at REAL, updated_at REAL)"
    )
    migrate(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(swarm_tasks)")}
    assert "tools_json" in cols
    migrate(conn)
