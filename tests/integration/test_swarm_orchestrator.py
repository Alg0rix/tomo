"""Opt-in team execution and session-local worker lifecycle."""

from __future__ import annotations

import asyncio
import sqlite3
import pytest

from app.models.mixins import swarm as swarm_store
from app.models.schema import migrate
from app.runtime.coordinator import swarm
from app.runtime.llm.base import LLMResponse
from app.runtime.tools import swarm_board
from app.services.chat import run_session_turn
from app.services.store import store


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


async def test_advisor_never_grants_consent(tmp_path, monkeypatch) -> None:
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
    assert advice[0] == "propose"
    assert store.with_db(lambda conn: swarm_store.list_runs(conn, session_id)) == []


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
         "tools": ["bash", "portal"]},
    ]}
    seen = []

    async def fake_plan(*args, **kwargs):
        return {}

    async def fake_turn(message, *, agent_id, **kwargs):
        if "Assigned swarm task" in kwargs.get("system_prompt", ""):
            names = {s["function"]["name"] for s in kwargs["tools"]}
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
    assert task["tools"] == ["bash", "portal"]


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
