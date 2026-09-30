"""Opt-in team execution and session-local worker lifecycle."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import pytest

from app.models.mixins import swarm as swarm_store
from app.models.schema import migrate
from app.runtime.coordinator import swarm
from app.runtime.llm.base import LLMResponse
from app.runtime.llm.base import ToolCall
from app.runtime.tools import swarm_board
from app.services.chat import run_session_turn
from app.services.store import store


async def test_single_agent_chat_can_delegate_repeatedly_without_mentions(tmp_path, monkeypatch) -> None:
    from tests.fakes.llm import ScriptedLLM, text_reply

    store.rebind(tmp_path / "single_chat_delegate.db")
    sid = store.get_or_create_session("main", "web")
    store.update_agent("research", {"enabled": False})

    async def no_swarm(*args, **kwargs):
        return None

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
    monkeypatch.setattr("app.channels.web.advise_swarm", no_swarm)
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


async def test_root_agent_can_start_real_workers_when_advisor_misses(tmp_path, monkeypatch) -> None:
    """Exercise the real tool loop, handoff, scheduler and synthesis, not a fake dispatch."""
    from app.runtime.agent import loop
    from tests.fakes.llm import ScriptedLLM, text_reply

    store.rebind(tmp_path / "handoff.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    request = "coba lu bikin swarm buat riset MCP"
    plan_calls = 0

    async def missed_intent(*args, **kwargs):
        return None

    async def plan(*args, **kwargs):
        nonlocal plan_calls
        plan_calls += 1
        if plan_calls > 1:
            return {}
        return {"agents": [], "tasks": [
            {"key": "architecture", "agent_id": "ops", "brief": "Review MCP architecture"},
            {"key": "tools", "agent_id": "research", "brief": "Review MCP tools"},
        ]}

    main_llm = ScriptedLLM([
        LLMResponse(content=None, tool_calls=[
            ToolCall(id="swarm", name="start_swarm", arguments={"request": request}),
            ToolCall(id="skip", name="write_file", arguments={"path": "must-not-exist", "content": "oops"}),
        ]),
        text_reply("Combined architecture and tools findings"),
    ])
    workers = {"ops": ScriptedLLM([text_reply("Architecture finding")]),
               "research": ScriptedLLM([text_reply("Tools finding")])}

    def get_llm(agent_id=None, **kwargs):
        return workers.get(agent_id, main_llm)

    monkeypatch.setattr("app.channels.web.advise_swarm", missed_intent)
    monkeypatch.setattr(swarm, "_plan", plan)
    monkeypatch.setattr(loop, "get_llm", get_llm)
    chunks = [chunk async for chunk in run_session_turn(sid, request, "web", start_seq=0)]
    runs = store.with_db(lambda c: swarm_store.list_runs(c, sid))
    assert len(runs) == 1
    assert runs[0]["status"] == "done"
    assert runs[0]["result"] == "Combined architecture and tools findings"
    tasks = store.with_db(lambda c: swarm_store.list_tasks(c, runs[0]["id"]))
    assert len(tasks) == 2
    assert all(task["status"] == "done" for task in tasks)
    assert any("swarm.event" in chunk and "task_started" in chunk for chunk in chunks)
    tools = [entry["function"] for entry in store.get_session_history(sid) if entry["type"] == "tool_call"]
    assert "start_swarm" in tools
    assert "write_file" not in tools
    assert main_llm.remaining == 0


@pytest.mark.parametrize("attachment_ids", [None, ["attachment_a"]])
async def test_explicit_swarm_request_routes_before_solo(tmp_path, monkeypatch, attachment_ids) -> None:
    store.rebind(tmp_path / "explicit_swarm_route.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    request = "coba lu bikin swarm buat audit cadesia.com"
    calls = []

    async def unexpected_solo(*args, **kwargs):
        pytest.fail("explicit swarm request must not enter a solo turn")
        yield

    async def fake_plan(*args, **kwargs):
        return {"decision": "run", "consent_quote": "bikin swarm", "tasks": []}

    async def fake_swarm(request_text, **kwargs):
        calls.append(request_text)
        yield {"kind": "final", "content": "Audit complete"}

    monkeypatch.setattr("app.channels.web._agent_run_turn", unexpected_solo)
    monkeypatch.setattr(swarm, "_plan", fake_plan)
    monkeypatch.setattr("app.channels.web.run_swarm_turn", fake_swarm)
    monkeypatch.setattr("app.services.chat.attachment_meta_for_ids", lambda ids: [])
    async for _ in run_session_turn(
        session_id, request, "web", start_seq=0, attachment_ids=attachment_ids,
    ):
        pass
    assert calls == [request]
    users = [entry for entry in store.get_session_history(session_id) if entry["type"] == "user"]
    assert users[-1]["execution_mode"] == "swarm"


@pytest.mark.parametrize("tasks", [None, [], [{"agent_id": "missing", "brief": "Audit"}]])
async def test_explicit_consent_routes_even_without_valid_worker_plan(tmp_path, monkeypatch, tasks) -> None:
    store.rebind(tmp_path / "intent.db")
    session_id = store.create_swarm_session(["main"], user_id="web")

    async def fake_plan(*args, **kwargs):
        return {"decision": "run", "consent_quote": "bikin swarm", "tasks": tasks}

    monkeypatch.setattr(swarm, "_plan", fake_plan)
    advice = await swarm.advise_swarm(
        "coba lu bikin swarm buat audit cadesia.com",
        session_id=session_id, coordinator_id="main",
    )
    assert advice == ("run", {}, "")
    assert store.with_db(lambda conn: swarm_store.list_runs(conn, session_id)) == []


async def test_ungrounded_consent_does_not_route(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "ungrounded_intent.db")
    session_id = store.create_swarm_session(["main"], user_id="web")

    async def fake_plan(*args, **kwargs):
        return {"decision": "run", "consent_quote": "use a swarm", "tasks": []}

    monkeypatch.setattr(swarm, "_plan", fake_plan)
    assert await swarm.advise_swarm(
        "audit cadesia.com", session_id=session_id, coordinator_id="main",
    ) is None


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
    plan_calls = 0

    async def fake_plan(*args, **kwargs):
        nonlocal plan_calls
        plan_calls += 1
        return plan if plan_calls == 1 else {}

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

    monkeypatch.setattr(swarm, "_plan", fake_plan)
    monkeypatch.setattr(swarm, "run_turn", fake_turn)

    events = [ev async for ev in swarm.run_swarm_turn(
        "Review this", session_id=session_id, coordinator_id="main", history=[]
    )]
    assert peak == 2
    assert plan_calls >= 2, "coordinator should replan as workers finish"
    assert len([e for e in events if e.get("event") == "task_created"]) == 2
    assert len([e for e in events if e.get("event") == "task_done"]) == 2
    assert events[-1]["event"] == "run_done"
    assert store.get_session(session_id)["agent_ids"] == ["main"]
    runs = store.with_db(lambda conn: swarm_store.list_runs(conn, session_id))
    assert runs[0]["status"] == "done"
    assert runs[0]["result"] == "Combined answer"
    workers = store.with_db(lambda conn: swarm_store.list_agents(conn, session_id))
    assert {a["name"] for a in workers} == {"Reviewer A", "Reviewer B"}


async def test_advisor_selects_useful_independent_workers_without_chat_consent(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "advice.db")
    session_id = store.create_swarm_session(["main"], user_id="web")

    async def fake_plan(*args, **kwargs):
        return {"decision": "run", "agents": [], "tasks": [
            {"key": "a", "agent_id": "ops", "brief": "Inspect A"},
            {"key": "b", "agent_id": "research", "brief": "Inspect B"},
        ]}

    monkeypatch.setattr(swarm, "_plan", fake_plan)
    advice = await swarm.advise_swarm(
        "Investigate two independent concerns", session_id=session_id,
        coordinator_id="main",
    )
    assert advice is not None
    assert advice[0] == "run"
    assert len(advice[1]["tasks"]) == 2
    assert store.with_db(lambda conn: swarm_store.list_runs(conn, session_id)) == []


async def test_automatic_split_dispatches_real_workers_without_chat_approval(tmp_path, monkeypatch) -> None:
    from tests.fakes.llm import ScriptedLLM, text_reply

    store.rebind(tmp_path / "automatic_swarm.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    request = "Investigate API correctness and UI accessibility separately"
    plan = {"agents": [], "tasks": [
        {"key": "api", "agent_id": "ops", "brief": "Review API correctness", "tools": []},
        {"key": "ui", "agent_id": "research", "brief": "Review UI accessibility", "tools": []},
    ]}

    async def fake_plan(*args, **kwargs):
        return {"decision": "run", **plan} if kwargs.get("advisory") else {}

    clients = {"ops": ScriptedLLM([text_reply("API finding")]),
               "research": ScriptedLLM([text_reply("UI finding")]),
               "main": ScriptedLLM([text_reply("Combined findings")])}
    monkeypatch.setattr(swarm, "_plan", fake_plan)
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id, **kwargs: clients[agent_id])
    chunks = [chunk async for chunk in run_session_turn(sid, request, "web", start_seq=0)]
    runs = store.with_db(lambda conn: swarm_store.list_runs(conn, sid))
    assert len(runs) == 1
    assert runs[0]["status"] == "done"
    assert runs[0]["result"] == "Combined findings"
    tasks = store.with_db(lambda conn: swarm_store.list_tasks(conn, runs[0]["id"]))
    assert len(tasks) == 2
    assert all(task["status"] == "done" for task in tasks)
    assert store.with_db(lambda conn: swarm_store.get_proposal(conn, sid)) is None
    assert any("task_started" in chunk for chunk in chunks)
    assert all(client.remaining == 0 for client in clients.values())


async def test_clarify_answer_can_dispatch_without_another_chat_message(tmp_path, monkeypatch) -> None:
    from app.runtime.permissions import hitl
    from tests.fakes.llm import ScriptedLLM, text_reply

    store.rebind(tmp_path / "clarify_dispatch.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    request = "Review the supplied evidence"

    async def missed_intent(*args, **kwargs):
        return None

    async def fake_plan(*args, **kwargs):
        if args[4]:
            return {}
        return {"agents": [], "tasks": [
            {"key": "review", "agent_id": "research", "brief": "Review supplied evidence", "tools": []},
        ]}

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
            arguments={"request": request + "; clarify answer: Evidence and coverage"},
        )]),
        text_reply("Reviewed evidence and coverage"),
    ])
    worker = ScriptedLLM([text_reply("Evidence finding")])
    monkeypatch.setattr("app.channels.web.advise_swarm", missed_intent)
    monkeypatch.setattr(swarm, "_plan", fake_plan)
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
    assert runs[0]["result"] == "Reviewed evidence and coverage"
    assert len([e for e in store.get_session_history(sid) if e["type"] == "user"]) == 1
    assert main.remaining == worker.remaining == 0


async def test_explicit_team_request_in_plain_chat_is_recognized(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "explicit_team.db")
    session_id = store.create_swarm_session(["main"], user_id="web")

    async def fake_plan(*args, **kwargs):
        return {"decision": "run", "consent_quote": "pakai beberapa agent",
                "agents": [], "tasks": [
                    {"key": "research", "agent_id": "research", "brief": "Find prior art"},
                ]}

    monkeypatch.setattr(swarm, "_plan", fake_plan)
    advice = await swarm.advise_swarm(
        "Tolong pakai beberapa agent untuk riset ini",
        session_id=session_id, coordinator_id="main",
    )
    assert advice is not None
    assert advice[0] == "run"


async def test_chat_approval_runs_pending_plan_without_slash(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "chat_consent.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    request = "Investigate the API and UI separately"
    plan = {"agents": [], "tasks": [
        {"key": "api", "agent_id": "ops", "brief": "Review API"},
        {"key": "ui", "agent_id": "research", "brief": "Review UI"},
    ]}

    async def fake_advice(*args, **kwargs):
        return "propose", plan, "I can split API and UI. Gas?"

    calls = []

    async def fake_swarm(request_text, **kwargs):
        calls.append((request_text, kwargs["initial_plan"]))
        yield {"kind": "final", "content": "Reviewed both"}

    monkeypatch.setattr("app.channels.web.advise_swarm", fake_advice)
    monkeypatch.setattr("app.channels.web.run_swarm_turn", fake_swarm)
    for message in (request, "gas"):
        async for _ in run_session_turn(session_id, message, "web", start_seq=0):
            pass
    assert calls == [(request, plan)]
    assert store.with_db(lambda conn: swarm_store.get_proposal(conn, session_id)) is None


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


@pytest.mark.parametrize("proposal", [
    {},
    {"decision": "solo", "agents": [], "tasks": []},
    {"agents": [], "tasks": [{"key": "bad", "agent_id": "missing", "brief": "Review"}]},
])
async def test_dispatch_failure_never_falls_back_to_solo(tmp_path, monkeypatch, proposal) -> None:
    store.rebind(tmp_path / "dispatch_failure.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    attempts = []

    async def fake_plan(*args, **kwargs):
        attempts.append(kwargs.get("planning_feedback"))
        return proposal

    async def unexpected_turn(*args, **kwargs):
        pytest.fail("failed dispatch must not become a simulated solo answer")
        yield

    monkeypatch.setattr(swarm, "_plan", fake_plan)
    monkeypatch.setattr(swarm, "run_turn", unexpected_turn)
    events = [ev async for ev in swarm.run_swarm_turn(
        "Bikin swarm untuk review", session_id=sid, coordinator_id="main", history=[]
    )]
    assert len(attempts) == swarm.MAX_PLAN_CALLS
    assert attempts[0] == ""
    assert all(attempts[1:])
    assert any(ev.get("kind") == "error" and "No workers were started" in ev["message"]
               for ev in events)
    assert events[-1]["event"] == "run_done"
    assert events[-1]["status"] == "failed"
    assert not any(ev.get("kind") == "final" for ev in events)
    runs = store.with_db(lambda conn: swarm_store.list_runs(conn, sid))
    assert runs[0]["status"] == "failed"
    assert "valid worker plan" in runs[0]["result"]
    assert store.with_db(lambda conn: swarm_store.list_tasks(conn, runs[0]["id"])) == []


async def test_invalid_initial_plan_is_repaired_before_dispatch(tmp_path, monkeypatch) -> None:
    from tests.fakes.llm import ScriptedLLM, text_reply

    store.rebind(tmp_path / "dispatch_repair.db")
    sid = store.create_swarm_session(["main"], user_id="web")
    calls = []
    outputs = iter([
        {"tasks": [{"key": "bad", "agent_id": "missing", "brief": "Review"}]},
        {"agents": [{"name": "Reviewer", "purpose": "Review", "base_agent_id": "main"}],
         "tasks": [{"key": "review", "agent_id": "Reviewer", "brief": "Inspect supplied evidence",
                    "tools": []}]},
        {},
    ])

    async def fake_plan(*args, **kwargs):
        calls.append(kwargs.get("planning_feedback"))
        return next(outputs)

    llm = ScriptedLLM([text_reply("Verified evidence"), text_reply("Combined real worker result")])
    monkeypatch.setattr(swarm, "_plan", fake_plan)
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda *args, **kwargs: llm)
    events = [ev async for ev in swarm.run_swarm_turn(
        "Bikin swarm untuk review", session_id=sid, coordinator_id="main", history=[]
    )]
    assert calls[0] == ""
    assert calls[1]
    assert calls[2] == ""
    assert any(ev.get("event") == "task_started" for ev in events)
    assert events[-1]["status"] == "done"
    runs = store.with_db(lambda conn: swarm_store.list_runs(conn, sid))
    tasks = store.with_db(lambda conn: swarm_store.list_tasks(conn, runs[0]["id"]))
    assert len(tasks) == 1
    assert tasks[0]["status"] == "done"
    assert tasks[0]["result"] == "Verified evidence"
    assert runs[0]["result"] == "Combined real worker result"
    assert llm.remaining == 0


async def test_dependent_worker_waits_for_result(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "dependencies.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    plan = {"agents": [], "tasks": [
        {"key": "first", "agent_id": "ops", "brief": "Find the cause"},
        {"key": "second", "agent_id": "research", "brief": "Verify the cause",
         "depends_on": ["first"]},
    ]}
    seen = []

    async def fake_plan(*args, **kwargs):
        return {}

    async def fake_turn(message, *, agent_id, **kwargs):
        if "Assigned swarm task" in kwargs.get("system_prompt", ""):
            seen.append(("start", agent_id, message))
            await asyncio.sleep(0)
            seen.append(("done", agent_id, message))
            yield {"kind": "final", "content": f"Result from {agent_id}"}
        else:
            yield {"kind": "final", "content": "Verified"}

    monkeypatch.setattr(swarm, "_plan", fake_plan)
    monkeypatch.setattr(swarm, "run_turn", fake_turn)
    [e async for e in swarm.run_swarm_turn(
        "Find and verify", session_id=session_id, coordinator_id="main",
        history=[], initial_plan=plan,
    )]
    assert [kind + ":" + agent for kind, agent, _ in seen] == [
        "start:ops", "done:ops", "start:research", "done:research",
    ]
    assert "Result from ops" in seen[2][2]


async def test_coordinator_selects_bash_and_portal_for_worker(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "selected_tools.db")
    session_id = store.create_swarm_session(["main"], user_id="web")
    plan = {"agents": [], "tasks": [
        {"key": "prod_a", "agent_id": "ops", "brief": "Inspect prod A",
         "tools": ["bash", "portal", "swarm_board"]},
    ]}
    seen = []

    async def fake_plan(*args, **kwargs):
        return {}

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

    monkeypatch.setattr(swarm, "_plan", fake_plan)
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

    async def fake_plan(*args, **kwargs):
        return {}

    async def fake_turn(message, *, agent_id, **kwargs):
        if "Assigned swarm task" in kwargs.get("system_prompt", ""):
            swarm_board.run({"action": "publish", "content": "H100 is $2.49/hr"})
            yield {"kind": "tool_result", "tool": "swarm_board", "result": "Saved"}
            yield {"kind": "final", "content": "Prices collected"}
        else:
            yield {"kind": "final", "content": "Summary"}

    monkeypatch.setattr(swarm, "_plan", fake_plan)
    monkeypatch.setattr(swarm, "run_turn", fake_turn)
    events = [e async for e in swarm.run_swarm_turn(
        "Price GPUs", session_id=session_id, coordinator_id="main",
        history=[], initial_plan=plan,
    )]
    kinds = [e.get("event") for e in events if e.get("kind") == "swarm_event"]
    finding = next(e for e in events if e.get("event") == "finding")
    assert finding["content"] == "H100 is $2.49/hr" and finding["task_id"]
    assert kinds.index("finding") < kinds.index("task_done")
    assert kinds.index("phase") < kinds.index("task_created")
    phases = [e["phase"] for e in events if e.get("event") == "phase"]
    assert phases == ["planning", "synthesizing"]
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


async def test_planner_sees_enabled_tool_catalog(tmp_path) -> None:
    store.rebind(tmp_path / "planner_tools.db")
    session_id = store.create_swarm_session(["main"], user_id="web")

    class Planner:
        async def complete(self, messages, tools):
            import json

            payload = json.loads(messages[1]["content"])
            ops = next(a for a in payload["configured_agents"] if a["id"] == "ops")
            assert {"bash", "portal"} <= set(ops["tools"])
            assert "choose the few enabled tools" in messages[0]["content"].lower()
            return LLMResponse(content='{"decision":"solo","agents":[],"tasks":[]}',
                               tool_calls=[])

    plan = await swarm._plan("main", "Inspect prod A", session_id, "", {}, set(),
                             planner=Planner(), advisory=True)
    assert plan["decision"] == "solo"
