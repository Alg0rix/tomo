"""Restart recovery through SQLite, model turns and the Telegram dispatcher."""

import asyncio
import copy
import hashlib

import httpx
import pytest

from app.channels.telegram import TelegramAPI, TelegramDispatcher
from app.runtime.permissions import hitl
from app.services import chat, store, turn_recovery
from tests.fakes.llm import ScriptedLLM, bash_call, text_reply
from tests.unit.channels.test_telegram_ux import Bot, message, until


class PausedModel(ScriptedLLM):
    def __init__(self, responses, pause_round=1):
        super().__init__(responses)
        self.pause_round = pause_round
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, messages, tools=None):
        self.calls.append(copy.deepcopy(messages))
        if len(self.calls) == self.pause_round:
            self.started.set()
            await self.release.wait()
        return await super().complete(messages, tools)


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "recovery.db"
    store.rebind(path)
    store.update_settings(
        {
            "telegram_allowed_chat_ids": [-100, 42],
            "approvals_mode": "smart",
            "learning_enabled": False,
        }
    )
    hitl.clear_all_pending()
    chat._shutting_down = False
    bot = Bot()
    api = TelegramAPI("test-token", transport=httpx.MockTransport(bot.transport))
    yield path, bot, api
    chat._shutting_down = False
    hitl.clear_all_pending()


async def test_telegram_continues_after_repeated_restarts(setup, monkeypatch):
    path, bot, api = setup
    llm = PausedModel(
        [bash_call("printf done"), text_reply("old answer")], pause_round=2
    )
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None: llm)
    executed = []

    async def execute(name, args):
        executed.append(name)
        return "done"

    monkeypatch.setattr("app.runtime.agent.loop.execute_async", execute)
    dispatcher = TelegramDispatcher(api)
    inbound = message("do the task", chat=-100)
    inbound["message"]["message_thread_id"] = 7
    try:
        await dispatcher.dispatch(inbound)
        await asyncio.wait_for(llm.started.wait(), 3)
        sid = store.find_session("main", "tg_-100")
        assert executed == ["bash"]
        for _ in range(2):
            await chat.suspend_session_turns()
            await dispatcher.close()
            assert len(turn_recovery.pending_requests()) == 1
            store.rebind(path)  # New connection + loss of all process-local leases.
            hitl.clear_all_pending()
            await chat.recover_web_turns()
            llm = PausedModel([text_reply("continued answer")])
            dispatcher = TelegramDispatcher(api)
            dispatcher.recover_pending()
            await asyncio.wait_for(llm.started.wait(), 3)
            assert dispatcher.actors[-100] == (42, 7)
            assert dispatcher.uis[-100].session_id == sid
            prompt = llm.calls[0]
            assert any(m["role"] == "tool" and m["content"] == "done" for m in prompt)
            assert any("Tomo restarted" in str(m.get("content")) for m in prompt)
            assert [
                e["content"]
                for e in store.get_session_history(sid)
                if e["type"] == "user"
            ] == ["do the task"]
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        assert executed == ["bash"]
        assert not turn_recovery.pending_requests()
        assert any(
            p.get("chat_id") == -100
            and p.get("message_thread_id") == 7
            and "continued answer" in p.get("text", "")
            for _, p in bot.calls
        )
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_recovered_telegram_task_can_be_stopped(setup, monkeypatch):
    path, _bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    turn_recovery.save_request(
        sid,
        {
            "message": "unfinished",
            "user_id": "tg_42",
            "delivery": {
                "channel": "telegram",
                "chat_id": 42,
                "actor_id": 42,
                "bot": hashlib.sha256(b"test-token").hexdigest(),
            },
        },
    )
    store.append_session_history(sid, {"type": "user", "content": "unfinished"})
    llm = PausedModel([text_reply("should not finish")])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None: llm)
    dispatcher = TelegramDispatcher(api)
    try:
        dispatcher.recover_pending()
        await asyncio.wait_for(llm.started.wait(), 3)
        # Another sender cannot stop the recovered task.
        await dispatcher.dispatch(message("/stop", actor=43))
        assert chat.get_active_session_turn(sid) is not None
        await dispatcher.dispatch(message("/stop"))
        await until(lambda: not dispatcher.tasks)
        store.rebind(path)
        assert not turn_recovery.pending_requests()
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_committed_telegram_answer_is_delivered_without_rerunning(
    setup, monkeypatch
):
    _path, bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    turn_recovery.save_request(
        sid,
        {
            "message": "task",
            "user_id": "tg_42",
            "delivery": {
                "channel": "telegram",
                "chat_id": 42,
                "actor_id": 42,
                "bot": hashlib.sha256(b"test-token").hexdigest(),
            },
        },
    )
    store.append_session_history(sid, {"type": "user", "content": "task"})
    store.append_session_history(
        sid, {"type": "final", "content": "already finished", "agent_id": "main"}
    )
    # Empty response queue fails if recovery invokes the LLM.
    llm = ScriptedLLM([])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None: llm)
    dispatcher = TelegramDispatcher(api)
    try:
        dispatcher.recover_pending()
        await until(lambda: not dispatcher.tasks)
        assert any("already finished" in p.get("text", "") for _, p in bot.calls)
        assert len(store.get_session_history(sid)) == 2
        assert not turn_recovery.pending_requests()
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_stop_before_runner_starts_discards_checkpoint(setup):
    _path, _bot, api = setup
    sid = store.create_swarm_session(["main"])
    try:
        turn, _queue = await chat.start_session_turn(sid, "do not run", "web")
        assert chat.cancel_session_turn(sid)
        with pytest.raises(asyncio.CancelledError):
            await turn.task
        assert not turn_recovery.pending_requests()
        assert not store.is_session_turn_active(sid)
    finally:
        await api.aclose()


async def test_recovery_requires_same_bot_and_current_chat_permission(setup):
    _path, bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    turn_recovery.save_request(
        sid,
        {
            "message": "task",
            "user_id": "tg_42",
            "delivery": {
                "channel": "telegram",
                "chat_id": 42,
                "actor_id": 42,
                "bot": hashlib.sha256(b"test-token").hexdigest(),
            },
        },
    )
    other = TelegramAPI("another-bot", transport=httpx.MockTransport(bot.transport))
    try:
        dispatcher = TelegramDispatcher(other)
        dispatcher.recover_pending()
        assert not dispatcher.tasks
        assert turn_recovery.pending_requests()
        store.update_settings({"telegram_allowed_chat_ids": []})
        dispatcher = TelegramDispatcher(api)
        dispatcher.recover_pending()
        assert not dispatcher.tasks
        assert not turn_recovery.pending_requests()
        assert not bot.calls
    finally:
        await other.aclose()
        await api.aclose()


async def test_web_recovers_request_saved_before_input_was_recorded(setup, monkeypatch):
    _path, _bot, api = setup
    sid = store.create_swarm_session(["main"])
    store.append_session_history(sid, {"type": "final", "content": "previous answer"})
    turn_recovery.save_request(sid, {"message": "new task", "user_id": "web"})
    llm = ScriptedLLM([text_reply("new answer")])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None: llm)
    try:
        await chat.recover_web_turns()
        turn = chat.get_active_session_turn(sid)
        await turn.task
        history = store.get_session_history(sid)
        assert [e["content"] for e in history if e["type"] == "user"] == ["new task"]
        assert history[-1]["content"] == "new answer"
        assert not turn_recovery.pending_requests()
    finally:
        await api.aclose()
