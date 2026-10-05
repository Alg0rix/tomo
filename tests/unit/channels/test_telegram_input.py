"""Busy-input semantics against real model rounds, turn leases, and Telegram cards."""

import asyncio
import copy

import httpx2
import pytest

from app.channels.telegram import TelegramAPI, TelegramDispatcher
from app.runtime.permissions import hitl
from app.services import store
from tests.fakes.llm import ScriptedLLM, text_reply
from tests.unit.channels.test_telegram_ux import Bot, message, until


class PausedLLM(ScriptedLLM):
    def __init__(self, responses):
        super().__init__(responses)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []
        self.cancelled = False

    async def complete(self, messages, tools=None):
        self.calls.append(copy.deepcopy(messages))
        response = await super().complete(messages, tools)
        if len(self.calls) == 1:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return response


@pytest.fixture
def setup(tmp_path):
    store.rebind(tmp_path / "inputs.db")
    from tests.fakes.access import seed_telegram_accounts
    seed_telegram_accounts([42, 43, -100])
    store.update_settings(
        {
            "telegram_allowed_chat_ids": [42, 43, -100],
            "approvals_mode": "smart",
            "learning_enabled": False,
        }
    )
    hitl.clear_all_pending()
    bot = Bot()
    api = TelegramAPI("secret", transport=httpx2.MockTransport(bot.transport))
    dispatcher = TelegramDispatcher(api)
    yield bot, api, dispatcher
    hitl.clear_all_pending()
    store.update_settings({"learning_enabled": True})


def install(monkeypatch, responses):
    llm = PausedLLM([text_reply(s) for s in responses])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None, **kwargs: llm)
    return llm


def receipt(bot, phrase):
    return next(
        mid
        for mid, payload in bot.messages.items()
        if phrase in payload.get("text", "")
    )


async def test_steer_receipt_changes_only_when_agent_reads_it(setup, monkeypatch):
    bot, api, dispatcher = setup
    llm = install(monkeypatch, ["Initial response", "Steered response"])
    try:
        await dispatcher.dispatch(message("first task"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(message("/steer use Indonesian"))
        ack = receipt(bot, "Guidance received")
        assert "Waiting" in bot.messages[ack]["text"]
        assert len(llm.calls) == 1
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        assert "Agent read" in bot.messages[ack]["text"]
        assert len(llm.calls) == 2
        assert any(
            m["role"] == "user" and "use Indonesian" in str(m["content"])
            for m in llm.calls[1]
        )
        sid = store.find_session("main", "tg_42")
        assert any(
            e.get("steered") and e["content"] == "use Indonesian"
            for e in store.get_session_history(sid)
        )
        assert not dispatcher.steers
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_queue_runs_separate_turns_fifo_and_updates_receipts(setup, monkeypatch):
    bot, api, dispatcher = setup
    llm = install(monkeypatch, ["First answer", "Second answer", "Third answer"])
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(message("/queue second"))
        await dispatcher.dispatch(message("/queue third"))
        assert len(llm.calls) == 1
        assert [i["text"] for i in dispatcher.pending[42]] == ["second", "third"]
        assert dispatcher.uis[42].queue_depth == 2
        await dispatcher.dispatch(message("/queue list"))
        assert any("1. second\n2. third" in p.get("text", "") for _, p in bot.calls)
        queued_receipts = [i["feedback_id"] for i in dispatcher.pending[42]]
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        users = [
            e["content"] for e in store.get_session_history(sid) if e["type"] == "user"
        ]
        assert users == ["first", "second", "third"]
        assert len(llm.calls) == 3, [
            [m.get("content") for m in call if m["role"] == "user"]
            for call in llm.calls
        ]
        assert all("Completed" in bot.messages[mid]["text"] for mid in queued_receipts)
        assert not dispatcher.pending
        assert not store.is_session_turn_active(sid)
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_interrupt_releases_old_lease_replaces_queue_and_starts_new_instruction(
    setup, monkeypatch
):
    bot, api, dispatcher = setup
    llm = install(monkeypatch, ["Cancelled answer", "New answer"])
    try:
        await dispatcher.dispatch(message("old task"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(message("/queue obsolete task"))
        queue_ack = dispatcher.pending[42][0]["feedback_id"]
        await dispatcher.dispatch(message("/interrupt new priority"))
        await until(lambda: not dispatcher.tasks)
        assert llm.cancelled
        assert len(llm.calls) == 2
        sid = store.find_session("main", "tg_42")
        users = [
            e["content"] for e in store.get_session_history(sid) if e["type"] == "user"
        ]
        assert users == ["old task", "new priority"]
        assert "Cancelled" in bot.messages[queue_ack]["text"]
        assert not store.is_session_turn_active(sid)
        assert not dispatcher.replacements
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_stop_button_clears_waiting_tasks_without_starting_them(
    setup, monkeypatch
):
    bot, api, dispatcher = setup
    llm = install(monkeypatch, ["Cancelled"])
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(message("/queue second"))
        ui = dispatcher.uis[42]
        ack = dispatcher.pending[42][0]["feedback_id"]
        await dispatcher.dispatch(bot.callback(f"ts:{ui.token}", ui.status_id))
        await until(lambda: not dispatcher.tasks)
        assert len(llm.calls) == 1
        assert "Cancelled" in bot.messages[ack]["text"]
        assert not dispatcher.pending
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_inline_modes_are_bound_and_default_messages_follow_queue_mode(
    setup, monkeypatch
):
    bot, api, dispatcher = setup
    llm = install(monkeypatch, ["First", "Steered", "Second"])
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        ui = dispatcher.uis[42]
        await dispatcher.dispatch(
            bot.callback(f"tm:{ui.token}:queue", ui.status_id, actor=99)
        )
        assert dispatcher.modes.get(42, "steer") == "steer"
        await dispatcher.dispatch(bot.callback(f"tm:{ui.token}:queue", ui.status_id))
        assert dispatcher.modes[42] == "queue"
        await dispatcher.dispatch(message("second"))
        assert dispatcher.pending[42][0]["text"] == "second"
        await dispatcher.dispatch(message("/steer explicit guidance"))
        assert dispatcher.steers[42]
        # Explicit steering still enters the current run, despite queue default.
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        assert any("Agent read" in p.get("text", "") for p in bot.messages.values())
        await dispatcher.dispatch(
            bot.callback(f"tm:{ui.token}:interrupt", ui.status_id)
        )
        assert dispatcher.modes[42] == "queue"
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_queue_clear_bound_and_wrong_actor_cannot_inject(setup, monkeypatch):
    bot, api, dispatcher = setup
    llm = install(monkeypatch, ["First"])
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(message("/queue forbidden", actor=99))
        await dispatcher.dispatch(message("/interrupt forbidden", actor=99))
        assert not dispatcher.pending and not dispatcher.replacements
        for i in range(11):
            await dispatcher.dispatch(message("/queue task " + str(i)))
        assert len(dispatcher.pending[42]) == 10
        assert any("queue is full" in p.get("text", "") for _, p in bot.calls)
        await dispatcher.dispatch(message("/queue clear"))
        assert not dispatcher.pending
        assert not llm.cancelled
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        assert len(llm.calls) == 1
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_steer_during_startup_falls_back_to_followup_without_loss(
    setup, monkeypatch
):
    _, api, dispatcher = setup
    llm = install(monkeypatch, ["First", "Steered", "Second"])
    try:
        await dispatcher.dispatch(message("first"))
        await dispatcher.dispatch(message("/steer early guidance"))
        assert dispatcher.pending[42][0]["text"] == "early guidance"
        await asyncio.wait_for(llm.started.wait(), 2)
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        assert [
            e["content"] for e in store.get_session_history(sid) if e["type"] == "user"
        ] == ["first", "early guidance"]
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_idle_explicit_payload_is_content_not_nested_command(setup, monkeypatch):
    _, api, dispatcher = setup
    llm = install(monkeypatch, ["Payload processed"])
    try:
        await dispatcher.dispatch(message("/queue /new"))
        await asyncio.wait_for(llm.started.wait(), 2)
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        assert any(
            e["content"] == "/new"
            for e in store.get_session_history(sid)
            if e["type"] == "user"
        )
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_interrupt_before_driver_first_step_does_not_lose_replacement(
    setup, monkeypatch
):
    _, api, dispatcher = setup
    llm = install(monkeypatch, ["Replacement response"])
    try:
        await dispatcher.dispatch(message("original"))
        await dispatcher.dispatch(message("/interrupt replacement"))
        await asyncio.wait_for(llm.started.wait(), 2)
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        assert [
            e["content"] for e in store.get_session_history(sid) if e["type"] == "user"
        ] == ["replacement"]
        assert not dispatcher.replacements and not dispatcher.stopped
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_agent_commentary_visible_but_private_reasoning_never_sent(
    setup, monkeypatch
):
    from tests.fakes.llm import bash_call

    bot, api, dispatcher = setup
    response = bash_call("true")
    response.content = "I am checking the requested information."
    response.reasoning = "PRIVATE REASONING MUST NOT BE SENT"
    llm = ScriptedLLM([response, text_reply("Finished")])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None, **kwargs: llm)
    try:
        await dispatcher.dispatch(message("check"))
        await until(lambda: not dispatcher.tasks)
        visible = "\n".join(p.get("text", "") for _, p in bot.calls)
        assert "I am checking the requested information." in visible
        assert "PRIVATE REASONING" not in visible
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_interrupt_approval_wait_does_not_grant_old_permission(
    setup, monkeypatch
):
    from tests.fakes.llm import bash_call

    _, api, dispatcher = setup
    store.update_settings({"approvals_mode": "manual"})
    llm = ScriptedLLM([bash_call("rm -r approval-test"), text_reply("Replacement")])
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None, **kwargs: llm)
    try:
        await dispatcher.dispatch(message("old"))
        await until(lambda: 42 in dispatcher.uis and dispatcher.uis[42].waiting)
        old_ui = dispatcher.uis[42]
        sid = old_ui.session_id
        await dispatcher.dispatch(message("/interrupt new"))
        await until(lambda: not dispatcher.tasks)
        assert not hitl.list_pending_for_session(sid)["approvals"]
        assert not store.is_session_turn_active(sid)
        assert any(
            e["type"] == "user" and e["content"] == "new"
            for e in store.get_session_history(sid)
        )
        assert old_ui.finished and not old_ui.prompts
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_revoking_chat_discards_waiting_queue(setup, monkeypatch):
    bot, api, dispatcher = setup
    llm = install(monkeypatch, ["First"])
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(message("/queue forbidden after revoke"))
        ack = dispatcher.pending[42][0]["feedback_id"]
        store.update_settings({"telegram_allowed_chat_ids": []})
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        assert len(llm.calls) == 1
        assert not dispatcher.pending
        assert "Cancelled" in bot.messages[ack]["text"]
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_unconsumed_accepted_steer_becomes_followup(setup, monkeypatch):
    bot, api, dispatcher = setup
    llm = install(monkeypatch, ["First", "Follow-up"])
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        # Simulate an accepted steer missing the runner's final drain boundary.
        monkeypatch.setattr(
            "app.services.chat.push_session_steer",
            lambda *a, **k: {"accepted": True, "steer_id": "late"},
        )
        await dispatcher.dispatch(message("/steer late guidance"))
        ack = dispatcher.steers[42]["late"]["feedback_id"]
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        assert [
            e["content"] for e in store.get_session_history(sid) if e["type"] == "user"
        ] == ["first", "late guidance"]
        assert "Completed" in bot.messages[ack]["text"]
        assert not dispatcher.steers
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_consumed_before_ack_response_does_not_regress_feedback(
    setup, monkeypatch
):
    import json

    bot, old_api, old_dispatcher = setup
    llm = install(monkeypatch, ["First", "Steered"])
    dispatcher = None

    async def transport(request):
        payload = json.loads(request.content)
        if request.url.path.endswith(
            "/sendMessage"
        ) and "Guidance received" in payload.get("text", ""):
            llm.release.set()
            await until(lambda: not dispatcher.tasks)
        return bot.transport(request)

    api = TelegramAPI("secret", transport=httpx2.MockTransport(transport))
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(message("/steer faster"))
        assert any("Agent read" in p.get("text", "") for p in bot.messages.values())
        assert not any(
            "Waiting for the agent" in p.get("text", "") for p in bot.messages.values()
        )
    finally:
        await dispatcher.close()
        await api.aclose()
        await old_dispatcher.close()
        await old_api.aclose()


async def test_media_album_waits_without_download_and_runs_as_one_queued_turn(
    setup, monkeypatch, tmp_path
):
    from tests.unit.channels.test_telegram_media import MediaBot, media

    _, old_api, old_dispatcher = setup
    monkeypatch.setattr("app.core.config.TOMO_HOME", tmp_path)
    bot = MediaBot()
    api = TelegramAPI("secret", transport=httpx2.MockTransport(bot.transport))
    dispatcher = TelegramDispatcher(api)
    llm = install(monkeypatch, ["First", "Album"])
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(
            media("photo", caption="Compare", album="waiting", msg_id=2)
        )
        await dispatcher.dispatch(media("photo", album="waiting", msg_id=3))
        assert len(dispatcher.pending[42]) == 1
        assert not any(m == "getFile" for m, _ in bot.calls)
        llm.release.set()
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        users = [e for e in store.get_session_history(sid) if e["type"] == "user"]
        assert len(users) == 2 and len(users[1]["attachment_ids"]) == 2
        assert len([m for m, _ in bot.calls if m == "getFile"]) == 2
    finally:
        await dispatcher.close()
        await api.aclose()
        await old_dispatcher.close()
        await old_api.aclose()


async def test_subagent_feedback_does_not_expose_private_result_content(setup):
    from app.channels.sse_map import fmt_sse
    from app.channels.telegram_ui import TelegramTurnUI

    bot, api, dispatcher = setup
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid)
    try:
        await ui.start()
        await ui.consume(
            fmt_sse(
                {
                    "event": "subagent_done",
                    "data": {
                        "agent": "Researcher",
                        "content": "PRIVATE RESULT",
                        "status": "ok",
                        "delegate_call_id": "d1",
                    },
                }
            )
        )
        await ui.refresh()
        assert "Researcher finished" in bot.messages[ui.status_id]["text"]
        assert "PRIVATE RESULT" not in str(bot.calls)
    finally:
        await ui.close()
        await dispatcher.close()
        await api.aclose()


async def test_interrupt_during_queued_start_feedback_does_not_run_old_payload(
    setup, monkeypatch
):
    _, api, dispatcher = setup
    llm = install(monkeypatch, ["First", "Replacement"])
    feedback_started = asyncio.Event()
    release_feedback = asyncio.Event()
    edit = api.edit_message

    async def delayed_edit(chat, mid, text, **kwargs):
        if text == "▶ Agent started this task.":
            feedback_started.set()
            await release_feedback.wait()
        return await edit(chat, mid, text, **kwargs)

    monkeypatch.setattr(api, "edit_message", delayed_edit)
    try:
        await dispatcher.dispatch(message("first"))
        await asyncio.wait_for(llm.started.wait(), 2)
        await dispatcher.dispatch(message("/queue old queued instruction"))
        llm.release.set()
        await asyncio.wait_for(feedback_started.wait(), 2)
        assert 42 not in dispatcher.uis
        await asyncio.wait_for(
            dispatcher.dispatch(message("/interrupt replacement")), 1
        )
        release_feedback.set()
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        assert [
            e["content"] for e in store.get_session_history(sid) if e["type"] == "user"
        ] == ["first", "replacement"]
        assert len(llm.calls) == 2
    finally:
        release_feedback.set()
        await dispatcher.close()
        await api.aclose()
