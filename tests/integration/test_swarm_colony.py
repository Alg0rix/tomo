"""Two-way coordination using the real model loop and scoped board."""
from __future__ import annotations

import asyncio
import json


from app.models.mixins import swarm as db
from app.runtime.agent.loop import run_turn
from app.runtime.agent.subagent import current_depth
from app.runtime.coordinator import swarm
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.tools import swarm_board
from app.services.store import store
from tests.fakes.llm import ScriptedLLM, text_reply


def setup_run(tmp_path):
    store.rebind(tmp_path / "colony.db")
    sid = store.get_or_create_session("main", "web")
    rid = store.with_db(lambda c: db.create_run(c, sid, "Check contract"))
    tids = [store.with_db(lambda c: db.create_task(c, run_id=rid, agent_id="research",
            brief="Check", depends_on=[], write_scope=[], tools=[])) for _ in range(2)]
    return sid, rid, tids


def post(rid, kind, payload, tid=""):
    return store.with_db(lambda c: db.append_event(c, rid, kind, payload, tid))


async def test_worker_asks_main_and_receives_answer_before_resuming(tmp_path, monkeypatch):
    store.rebind(tmp_path / "question.db")
    sid = store.get_or_create_session("main", "web")
    reviews = []

    class Worker(ScriptedLLM):
        async def complete(self, messages, tools=None):
            assert current_depth() > 0
            if self.remaining == 1:
                assert any("Use contract v2" in str(m["content"]) for m in messages)
            return await super().complete(messages, tools)

    class Coordinator(ScriptedLLM):
        async def complete(self, messages, tools=None):
            assert {s["function"]["name"] for s in tools} == {"swarm_board"}
            if not reviews:
                snapshot = next(json.loads(m["content"]) for m in messages
                                if m["role"] == "user" and str(m["content"]).startswith('{"request":'))
                q = next(e for e in snapshot["board"] if e["kind"] == "question")
                reviews.append(q)
                return LLMResponse(content=None, tool_calls=[ToolCall(id="answer", name="swarm_board", arguments={
                    "action": "send", "to_agent_id": q["agent_id"], "to_task_id": q["task_id"],
                    "reply_to_event_id": q["event_id"], "content": "Use contract v2",
                })])
            return text_reply("Answered the contract question")

    worker = Worker([LLMResponse(content=None, tool_calls=[ToolCall(id="question", name="swarm_board",
                     arguments={"action": "ask", "content": "Which contract version?"})]), text_reply("Verified v2")])
    main = Coordinator([])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda aid, **kw: worker if aid == "research" else main)
    plan = {"tasks": [{"key": "contract", "agent_id": "research", "brief": "Check contract", "tools": []}]}
    async def collect():
        return [e async for e in swarm.run_swarm_turn("Check contract", session_id=sid,
                coordinator_id="main", history=[], initial_plan=plan)]
    events = await asyncio.wait_for(collect(), 5)
    assert any(e.get("event") == "question_resolved" and e.get("status") == "answered" for e in events)
    assert len(reviews) == 1
    assert any(e.get("event") == "question" for e in events)
    assert any(e.get("event") == "message_received" and e.get("agent_id") == "research" for e in events)
    assert not any(e.get("kind") == "final" for e in events)  # Main synthesis owns the final answer.
    result = json.loads(next(e["result"] for e in events if e["kind"] == "swarm_result"))
    assert result["status"] == "done"
    assert result["tasks"][0]["result"] == "Verified v2"


async def test_delivery_is_task_scoped_once_and_late_guidance_reopens_final(tmp_path):
    sid, rid, (tid, other) = setup_run(tmp_path)
    private = post(rid, "message", {"agent_id": "main", "to_agent_id": "research",
                   "to_task_id": other, "content": "Private to sibling"})
    finding = post(rid, "finding", {"agent_id": "research", "content": "Shared evidence"}, other)
    token = swarm_board.bind(run_id=rid, task_id=tid, agent_id="research", coordinator_id="main")
    rounds = []
    try:
        class Worker(ScriptedLLM):
            async def complete(self, messages, tools=None):
                rounds.append(list(messages))
                if len(rounds) == 1:
                    post(rid, "message", {"agent_id": "main", "to_agent_id": "research",
                         "to_task_id": tid, "content": "Correct the conclusion"})
                return await super().complete(messages, tools)
        events = [e async for e in run_turn("Check", agent_id="research", session_id=sid,
                  llm=Worker([text_reply("Old conclusion"), text_reply("Corrected conclusion")]),
                  tools=[], enable_atg=False)]
        assert len(rounds) == 2
        assert "Shared evidence" in str(rounds[0])
        assert "Private to sibling" not in str(rounds)
        assert "Correct the conclusion" in str(rounds[1])
        assert [e["content"] for e in events if e["kind"] == "final"] == ["Old conclusion", "Corrected conclusion"]
        assert next(e for e in events if e["kind"] == "final")["continued"]
        assert not swarm_board.pending()
        receipts = [e for e in store.with_db(lambda c: db.list_events(c, rid)) if e["kind"] == "message_received"]
        assert len(receipts) == 2
        assert finding in [e["payload"]["source_event_id"] for e in receipts]
        assert private not in [e["payload"]["source_event_id"] for e in receipts]
    finally:
        swarm_board.reset(token)


async def test_question_timeout_and_invalid_recipient_are_honest(tmp_path, monkeypatch):
    _, rid, (tid, other) = setup_run(tmp_path)
    token = swarm_board.bind(run_id=rid, task_id=tid, agent_id="research", coordinator_id="main")
    try:
        assert "roster" in swarm_board.run({"action": "send", "to_agent_id": "unknown", "content": "Question"})
        assert "target agent" in swarm_board.run({"action": "send", "to_agent_id": "main", "to_task_id": other, "content": "Question"})
        assert "Saved" in swarm_board.run({"action": "ask", "content": "Need approval"})
        monkeypatch.setattr(swarm_board, "QUESTION_TIMEOUT", 0)
        messages = []
        resolved = [e async for e in swarm_board.deliver(messages)]
        assert len(resolved) == 1
        assert resolved[0]["event"] == "question_resolved"
        assert resolved[0]["status"] == "timeout"
        assert "unresolved" in messages[-1]["content"]
        assert "invent approval" in messages[-1]["content"]
        store.with_db(lambda c: db.update_task(c, other, "done"))
        assert "already ended" in swarm_board.run({"action": "send", "to_agent_id": "research", "to_task_id": other, "content": "Late"})
    finally:
        swarm_board.reset(token)


async def test_cancel_stops_question_wait_and_coordinator_review(tmp_path, monkeypatch):
    store.rebind(tmp_path / "cancel_colony.db")
    sid = store.get_or_create_session("main", "web")
    reviewing = asyncio.Event()
    stopped = asyncio.Event()
    worker = ScriptedLLM([LLMResponse(content=None, tool_calls=[ToolCall(id="ask", name="swarm_board",
                           arguments={"action": "ask", "content": "Need a decision"})])])
    real_turn = swarm.run_turn
    async def turn(message, **kw):
        if kw["agent_id"] == "main":
            # Start quietly after the stream has drained the worker's output.
            await asyncio.sleep(0.01)
            reviewing.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
            return
        async for e in real_turn(message, llm=worker, enable_atg=False, **kw):
            yield e
    monkeypatch.setattr(swarm, "run_turn", turn)
    plan = {"tasks": [{"key": "check", "agent_id": "research", "brief": "Check", "tools": []}]}
    gen = swarm.run_swarm_turn("Check", session_id=sid, coordinator_id="main", history=[], initial_plan=plan)
    async def consume():
        async for _ in gen:
            pass
    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(reviewing.wait(), 3)
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
    assert stopped.is_set()
    run = store.with_db(lambda c: db.list_runs(c, sid))[0]
    assert run["status"] == "cancelled"
    assert store.with_db(lambda c: db.list_tasks(c, run["id"]))[0]["status"] == "cancelled"


async def test_repeated_reviews_preserve_exact_cache_prefix_and_tools(tmp_path):
    import copy
    setup_run(tmp_path)
    captured = []
    class Model(ScriptedLLM):
        async def complete(self, messages, tools=None):
            captured.append((copy.deepcopy(messages), copy.deepcopy(tools)))
            return await super().complete(messages, tools)
    llm = Model([text_reply("First review"), text_reply("Second review")])
    conversation = []
    for update in ("Initial evidence", "New evidence"):
        async for _ in run_turn(update, llm=llm, agent_id="main", tools=[],
                                system_prompt="Stable coordination instructions", conversation=conversation,
                                enable_atg=False):
            pass
    previous = captured[0][0] + [{"role": "assistant", "content": "First review"}]
    assert captured[1][0][:-1] == previous
    assert captured[1][0][-1] == {"role": "user", "content": "New evidence"}
    assert captured[0][1] == captured[1][1]
    assert conversation[-1] == {"role": "assistant", "content": "Second review"}


async def test_user_steer_is_consumed_by_main_and_translated_for_worker(tmp_path, monkeypatch):
    from app.services import chat
    store.rebind(tmp_path / "steer_colony.db")
    sid = store.get_or_create_session("main", "web")
    inbox = [{"content": "Only read production", "steer_id": "guidance", "attachment_ids": []}]
    def drain(_):
        result = list(inbox)
        inbox.clear()
        return result
    monkeypatch.setattr(chat, "drain_session_steers", drain)
    instructed = asyncio.Event()
    async def turn(message, **kw):
        if kw["agent_id"] == "main":
            snapshot = json.loads(message)
            assert "Only read production" in str(snapshot["user_updates"])
            task = snapshot["tasks"][0]
            assert "Saved" in swarm_board.run({"action": "send", "to_agent_id": task["agent_id"],
                    "to_task_id": task["id"], "content": "Use read-only checks; do not mutate production"})
            instructed.set()
            yield {"kind": "final", "content": "Translated guidance"}
        else:
            assert current_depth() > 0
            await asyncio.wait_for(instructed.wait(), 3)
            context = []
            async for ev in swarm_board.deliver(context):
                yield ev
            assert "read-only checks" in str(context)
            yield {"kind": "final", "content": "Read-only checks complete"}
    monkeypatch.setattr(swarm, "run_turn", turn)
    events = [e async for e in swarm.run_swarm_turn("Check production", session_id=sid,
                coordinator_id="main", history=[], initial_plan={"tasks": [{"key": "check",
                "agent_id": "research", "brief": "Check", "tools": []}]})]
    assert len([e for e in events if e["kind"] == "steer"]) == 1
    result = json.loads(next(e["result"] for e in events if e["kind"] == "swarm_result"))
    assert result["user_updates"] == [{"role": "user", "content": "Only read production"}]


async def test_quiet_review_records_usage_without_board_summary(tmp_path, monkeypatch):
    for actionable in (False, True):
        sid, rid, _ = setup_run(tmp_path)
        async def turn(message, **kw):
            assert "finish quietly" in kw["system_prompt"]
            assert "exactly NO_UPDATE" in kw["system_prompt"]
            assert "exactly COORDINATED" in kw["system_prompt"]
            if actionable:
                assert "Saved" in swarm_board.run({"action": "publish", "content": "New evidence changes the agreed contract"})
            yield {"kind": "final", "content": "COORDINATED" if actionable else "NO_UPDATE",
                   "metrics": {"prompt_tokens": 100, "cached_tokens": 80}}
        monkeypatch.setattr(swarm, "run_turn", turn)
        events = asyncio.Queue()
        await swarm._supervise(run_id=rid, session_id=sid, coordinator_id="main",
                               request="Check contract", tasks={}, guidance=[], events=events, conversation=[])
        emitted = [events.get_nowait() for _ in range(events.qsize())]
        assert [kind for kind, _, _ in emitted] == ["coordinator_review_done", "coordinator_finished"]
        assert emitted[0][2]["metrics"]["cached_tokens"] == 80
        assert "content" not in emitted[0][2]
        stored = store.with_db(lambda c: db.list_events(c, rid))
        assert all(e["kind"] != "coordinator_note" for e in stored)
        assert len([e for e in stored if e["kind"] == "finding"]) == int(actionable)
