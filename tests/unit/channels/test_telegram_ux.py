"""Telegram UI through the real turn manager, gate, history, and mocked Bot API."""

from __future__ import annotations

import asyncio
import json

import httpx2
import pytest

from app.channels.telegram import TelegramAPI, TelegramDispatcher
from app.channels.telegram_ui import TelegramTurnUI
from app.channels.sse_map import fmt_sse
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.permissions import hitl
from app.runtime.permissions.modes import clear_session_modes
from app.services import store
from tests.fakes.llm import ScriptedLLM, bash_call, text_reply


class Bot:
    def __init__(self):
        self.calls = []
        self.messages = {}
        self.counter = 100
        self.fail_prompt = False

    def transport(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        self.calls.append((method, payload))
        if method == "sendMessage":
            if self.fail_prompt and payload.get("reply_markup", {}).get(
                "inline_keyboard", [[]]
            )[0][0].get("callback_data", "").startswith("ta:"):
                return httpx2.Response(
                    403, json={"ok": False, "error_code": 403, "description": "blocked"}
                )
            self.counter += 1
            result = {
                "message_id": self.counter,
                "chat": {"id": payload["chat_id"]},
                **payload,
            }
            self.messages[self.counter] = result
        elif method == "deleteMessage":
            self.messages.pop(payload["message_id"], None)
            result = True
        elif method == "editMessageText":
            self.messages.setdefault(payload["message_id"], {}).update(payload)
            result = self.messages[payload["message_id"]]
        elif method == "editMessageReplyMarkup":
            self.messages.setdefault(payload["message_id"], {}).update(payload)
            result = {}
        elif method == "sendMessageDraft":
            # Legacy-server fake; RichBot opts in to native DM drafts.
            return httpx2.Response(404, json={"ok": False, "error_code": 404})
        elif method == "getUpdates":
            result = []
        else:
            result = True
        return httpx2.Response(200, json={"ok": True, "result": result})

    def callback(self, data, message_id, *, actor=42, chat=42, thread=None):
        message = {"message_id": message_id, "chat": {"id": chat}}
        if thread is not None:
            message["message_thread_id"] = thread
        return {
            "callback_query": {
                "id": "callback",
                "data": data,
                "from": {"id": actor},
                "message": message,
            }
        }


def message(text, *, chat=42, actor=42, reply_to=None):
    msg = {"chat": {"id": chat}, "from": {"id": actor}, "message_id": 1, "text": text}
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"message": msg}


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
def setup(tmp_path):
    store.rebind(tmp_path / "telegram-ux.db")
    store.update_settings(
        {"telegram_allowed_chat_ids": ["42", "43", "-100"], "approvals_mode": "manual"}
    )
    hitl.clear_all_pending()
    clear_session_modes()
    bot = Bot()
    api = TelegramAPI("not-a-real-token", transport=httpx2.MockTransport(bot.transport))
    yield bot, api
    hitl.clear_all_pending()
    clear_session_modes()
    store.update_settings({"approvals_mode": "smart"})


def inject(monkeypatch, responses):
    llm = ScriptedLLM(responses)
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None, **kwargs: llm)
    return llm


async def test_approval_deny_unblocks_real_turn_and_other_chat_stays_responsive(
    setup, monkeypatch
):
    bot, api = setup
    # Main has unrestricted paths without a project; use a flagged mutation.
    inject(
        monkeypatch,
        [
            bash_call("rm -r approval-test"),
            text_reply("Other chat ready"),
            text_reply("Denied safely."),
        ],
    )
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("remove the test directory"))
        await until(lambda: 42 in dispatcher.uis and bool(dispatcher.uis[42].prompts))
        ui = dispatcher.uis[42]
        prompt = next(iter(ui.prompts.values()))
        assert store.is_session_turn_active(ui.session_id)
        keyboard = bot.messages[prompt.message_id]["reply_markup"]["inline_keyboard"]
        deny = next(
            b["callback_data"] for row in keyboard for b in row if b["text"] == "Deny"
        )
        assert all(
            len(b["callback_data"].encode()) <= 64 for row in keyboard for b in row
        )
        await dispatcher.dispatch(
            message("hello from a second chat", chat=43, actor=43)
        )
        await until(
            lambda: store.find_session("main", "tg_43") and 43 not in dispatcher.tasks
        )
        assert any(
            "Other chat ready" in e.get("content", "")
            for e in store.get_session_history(store.find_session("main", "tg_43"))
        )
        await dispatcher.dispatch(bot.callback(deny, prompt.message_id))
        await until(lambda: not dispatcher.tasks)
        history = store.get_session_history(ui.session_id)
        assert any(
            e.get("type") == "tool_output" and "BLOCKED" in e.get("content", "")
            for e in history
        )
        assert any("Denied safely." in e.get("content", "") for e in history)
        assert not store.is_session_turn_active(ui.session_id)
        assert bot.messages[prompt.message_id]["reply_markup"]["inline_keyboard"] == []
        await dispatcher.dispatch(bot.callback(deny, prompt.message_id))
        assert bot.calls[-1][0] == "answerCallbackQuery"
        assert "expired" in bot.calls[-1][1]["text"]
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_clarify_buttons_and_free_text_complete_real_turn(setup, monkeypatch):
    bot, api = setup
    inject(
        monkeypatch,
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="q",
                        name="clarify",
                        arguments={
                            "question": "Which environment?",
                            "choices": ["Dev", "Prod"],
                        },
                    )
                ],
            ),
            text_reply("Using staging."),
        ],
    )
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("deploy where?"))
        await until(lambda: 42 in dispatcher.uis and bool(dispatcher.uis[42].prompts))
        ui = dispatcher.uis[42]
        prompt = next(iter(ui.prompts.values()))
        await dispatcher.dispatch(
            bot.callback(f"tc:{prompt.token}:other", prompt.message_id)
        )
        await dispatcher.dispatch(message("Staging", reply_to=prompt.message_id))
        await until(lambda: not dispatcher.tasks)
        history = store.get_session_history(ui.session_id)
        assert any(
            "Staging" in e.get("content", "")
            for e in history
            if e.get("type") == "tool_output"
        )
        assert any(
            "Using staging." in p["text"]
            for m, p in bot.calls
            if m in {"sendMessage", "editMessageText"}
        )
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_stop_button_interrupts_approval_wait_and_releases_lease(
    setup, monkeypatch
):
    bot, api = setup
    inject(monkeypatch, [bash_call("rm -r approval-test")])
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("inspect"))
        await until(lambda: 42 in dispatcher.uis and bool(dispatcher.uis[42].prompts))
        ui = dispatcher.uis[42]
        status_id = ui.status_id
        await dispatcher.dispatch(bot.callback(f"ts:{ui.token}", status_id))
        await until(lambda: not dispatcher.tasks)
        assert hitl.list_pending_for_session(ui.session_id) == {
            "approvals": [],
            "clarifies": [],
        }
        assert not store.is_session_turn_active(ui.session_id)
        assert any(
            "Stopped." in p["text"]
            for m, p in bot.calls
            if m in {"sendMessage", "editMessageText"}
        )
        assert status_id not in bot.messages
    finally:
        await dispatcher.close()
        await api.aclose()


@pytest.mark.parametrize(
    "spoof", ["actor", "chat", "message", "thread", "revoked", "choice"]
)
async def test_callback_authentication_fails_closed(setup, spoof):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_-100")
    ui = TelegramTurnUI(api, -100, sid, actor_id=42)
    payload = hitl.create_approval(
        tool="bash",
        args={"command": "ls /outside"},
        findings=[],
        description="outside",
        session_id=sid,
        allow_permanent=False,
    )
    await ui.show_prompt("approval", payload)
    prompt = next(iter(ui.prompts.values()))
    query = bot.callback(
        f"ta:{prompt.token}:once", prompt.message_id, actor=42, chat=-100
    )["callback_query"]
    if spoof == "actor":
        query["from"]["id"] = 99
    elif spoof == "chat":
        query["message"]["chat"]["id"] = 43
    elif spoof == "message":
        query["message"]["message_id"] += 1
    elif spoof == "thread":
        query["message"]["message_thread_id"] = 2
    elif spoof == "revoked":
        store.update_settings({"telegram_allowed_chat_ids": []})
    elif spoof == "choice":
        query["data"] = f"ta:{prompt.token}:always"
    try:
        assert await ui.callback(query)
        assert hitl.list_pending_for_session(sid)["approvals"]
        assert bot.calls[-1][0] == "answerCallbackQuery"
    finally:
        await ui.close()
        await api.aclose()


async def test_expired_request_cannot_be_approved(setup):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid)
    payload = hitl.create_approval(
        tool="bash", args={}, findings=[], description="test", session_id=sid
    )
    await ui.show_prompt("approval", payload)
    prompt = next(iter(ui.prompts.values()))
    assert await hitl.await_approval(payload["id"], timeout=0.01) == "deny"
    try:
        await ui.callback(
            bot.callback(f"ta:{prompt.token}:once", prompt.message_id)["callback_query"]
        )
        assert any(
            "expired" in p.get("text", "")
            for m, p in bot.calls
            if m == "answerCallbackQuery"
        )
        assert bot.messages[prompt.message_id]["reply_markup"]["inline_keyboard"] == []
    finally:
        await ui.close()
        await api.aclose()


async def test_activity_is_compact_and_does_not_publish_reasoning_or_tool_secrets(
    setup,
):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid)
    try:
        await ui.start()
        for i in range(8):
            await ui.consume(
                fmt_sse(
                    {
                        "event": "thinking_delta",
                        "data": {"content": "private-reasoning"},
                    }
                )
            )
            await ui.consume(
                fmt_sse(
                    {
                        "event": "tool",
                        "data": {
                            "tool": "bash",
                            "args": {"env": {"SECRET": "secret-value"}},
                            "call_id": str(i),
                        },
                    }
                )
            )
            await ui.consume(
                fmt_sse(
                    {
                        "event": "tool_result",
                        "data": {
                            "tool": "bash",
                            "result": "secret-output",
                            "call_id": str(i),
                            "error": i == 0,
                        },
                    }
                )
            )
        await ui.refresh()
        await ui.finish('**Complete**\n\n```python\nprint("ok")\n```')
        activity_sends = [
            p for m, p in bot.calls if m == "sendMessage" and p.get("reply_markup")
        ]
        assert len(activity_sends) == 1
        assert ui.completed == 8 and ui.failed == 1
        all_text = "\n".join(p.get("text", "") for _, p in bot.calls)
        assert "private-reasoning" not in all_text
        assert "secret-value" not in all_text and "secret-output" not in all_text
        assert "<b>Complete</b>" in all_text
        assert "<pre>" in all_text
    finally:
        await ui.close()
        await api.aclose()


async def test_shutdown_cancels_waiters_and_typing(setup, monkeypatch):
    _, api = setup
    inject(monkeypatch, [bash_call("rm -r approval-test")])
    dispatcher = TelegramDispatcher(api)
    await dispatcher.dispatch(message("inspect"))
    await until(lambda: 42 in dispatcher.uis and bool(dispatcher.uis[42].prompts))
    ui = dispatcher.uis[42]
    await dispatcher.close()
    assert not store.is_session_turn_active(ui.session_id)
    assert not dispatcher.tasks
    assert not hitl.list_pending_for_session(ui.session_id)["approvals"]
    assert ui._ticker.done()
    await api.aclose()


async def test_permanent_approval_requires_explicit_second_confirmation(setup):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid)
    payload = hitl.create_approval(
        tool="bash",
        args={},
        findings=[],
        description="Outside-jail access",
        session_id=sid,
    )
    await ui.show_prompt("approval", payload)
    prompt = next(iter(ui.prompts.values()))
    try:
        await ui.callback(
            bot.callback(f"ta:{prompt.token}:confirm_always", prompt.message_id)[
                "callback_query"
            ]
        )
        assert hitl.list_pending_for_session(sid)["approvals"]
        await ui.callback(
            bot.callback(f"ta:{prompt.token}:always", prompt.message_id)[
                "callback_query"
            ]
        )
        assert hitl.list_pending_for_session(sid)["approvals"]
        # Replaying the first tap must not confirm a persistent permission.
        await ui.callback(
            bot.callback(f"ta:{prompt.token}:always", prompt.message_id)[
                "callback_query"
            ]
        )
        assert hitl.list_pending_for_session(sid)["approvals"]
        await ui.callback(
            bot.callback(f"ta:{prompt.token}:confirm_always", prompt.message_id)[
                "callback_query"
            ]
        )
        assert await hitl.await_approval(payload["id"], timeout=0.1) == "always"
        assert bot.messages[prompt.message_id]["reply_markup"]["inline_keyboard"] == []
    finally:
        await ui.close()
        await api.aclose()


async def test_streamed_preview_delivered_as_full_unicode_answer_without_duplicate_pages(
    setup,
):
    from app.channels.telegram_format import plain_text, render_markdown, utf16_len

    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid)
    content = "```python\n" + "😀 < & >\n" * 1300 + "```"
    try:
        await ui.start()
        await ui.consume(
            fmt_sse(
                {"event": "delta", "data": {"agent_id": "main", "content": content}}
            )
        )
        await ui.refresh()
        answer_id = ui.answer_id
        assert answer_id is not None
        initial_ids = set(bot.messages)
        await ui.finish(content)
        assert answer_id not in bot.messages
        answer_pages = [
            bot.messages[i] for i in sorted(set(bot.messages) - initial_ids)
        ]
        assert "".join(plain_text(p["text"]) for p in answer_pages) == plain_text(
            render_markdown(content)
        )
        assert all(utf16_len(p["text"]) <= 3900 for p in answer_pages)
        assert not answer_pages[0]["disable_notification"]
        assert all(p["disable_notification"] for p in answer_pages[1:])
        assert (
            len(
                [
                    p
                    for m, p in bot.calls
                    if m == "sendMessage" and p.get("reply_markup")
                ]
            )
            == 1
        )
    finally:
        await ui.close()
        await api.aclose()


async def test_prompt_delivery_failure_denies_instead_of_hanging(setup):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid)
    payload = hitl.create_approval(
        tool="bash", args={}, findings=[], description="test", session_id=sid
    )
    bot.fail_prompt = True
    try:
        await ui.show_prompt("approval", payload)
        assert await hitl.await_approval(payload["id"], timeout=0.1) == "deny"
        assert not ui.prompts
    finally:
        await ui.close()
        await api.aclose()


async def test_no_group_member_can_answer_someone_elses_question(setup):
    _, api = setup
    sid = store.get_or_create_session("main", "tg_-100")
    ui = TelegramTurnUI(api, -100, sid, actor_id=42)
    payload = hitl.create_clarify(question="Which environment?", session_id=sid)
    await ui.show_prompt("clarify", payload)
    prompt = next(iter(ui.prompts.values()))
    try:
        assert not await ui.answer_text(
            message("Prod", chat=-100, actor=99, reply_to=prompt.message_id)["message"]
        )
        assert not await ui.answer_text(message("Prod", chat=-100, actor=42)["message"])
        assert await ui.answer_text(
            message("Dev", chat=-100, actor=42, reply_to=prompt.message_id)["message"]
        )
        assert await hitl.await_clarify(payload["id"], timeout=0.1) == "Dev"
    finally:
        await ui.close()
        await api.aclose()


async def test_typing_error_does_not_kill_progress_or_turn(setup, monkeypatch):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")

    async def broken(*args, **kwargs):
        raise RuntimeError("typing unavailable")

    monkeypatch.setattr(api, "send_typing", broken)
    ui = TelegramTurnUI(api, 42, sid)
    try:
        await ui.start()
        await asyncio.sleep(0.02)
        assert not ui._ticker.done()
        await ui.finish("Still answered.")
        assert any("Still answered." in p.get("text", "") for _, p in bot.calls)
    finally:
        await ui.close()
        await api.aclose()


async def test_real_poll_batch_does_not_wait_for_agent_approval(setup, monkeypatch):
    from app.channels.telegram import poll_once

    bot, api = setup
    inject(monkeypatch, [bash_call("rm -r approval-test"), text_reply("Denied.")])
    batches = [[{"update_id": 50, **message("inspect")}]]

    async def updates(**kwargs):
        return batches.pop(0) if batches else []

    monkeypatch.setattr(api, "get_updates", updates)
    dispatcher = TelegramDispatcher(api)
    try:
        async with asyncio.timeout(1):
            offset = await poll_once(api, timeout=0, dispatcher=dispatcher)
        assert offset == 51
        await until(lambda: 42 in dispatcher.uis and bool(dispatcher.uis[42].prompts))
        ui = dispatcher.uis[42]
        prompt = next(iter(ui.prompts.values()))
        batches.append(
            [
                {
                    "update_id": 51,
                    **bot.callback(f"ta:{prompt.token}:deny", prompt.message_id),
                }
            ]
        )
        assert (
            await poll_once(api, offset=offset, timeout=0, dispatcher=dispatcher) == 52
        )
        await until(lambda: not dispatcher.tasks)
        assert not store.is_session_turn_active(ui.session_id)
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_allow_once_dispatches_real_gate_and_records_tool_result(
    setup, monkeypatch
):
    bot, api = setup
    inject(monkeypatch, [bash_call("rm -r approval-test"), text_reply("Tool completed.")])
    executed = []

    async def execute(call, decision):
        assert decision.allowed
        executed.append(call.name)
        return "approved tool result"

    monkeypatch.setattr("app.runtime.agent.loop._execute_authorized", execute)
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("inspect"))
        await until(lambda: 42 in dispatcher.uis and bool(dispatcher.uis[42].prompts))
        ui = dispatcher.uis[42]
        prompt = next(iter(ui.prompts.values()))
        assert not executed
        await dispatcher.dispatch(
            bot.callback(f"ta:{prompt.token}:once", prompt.message_id)
        )
        await until(lambda: not dispatcher.tasks)
        assert executed == ["bash"]
        assert any(
            e.get("content") == "approved tool result"
            for e in store.get_session_history(ui.session_id)
        )
        assert ui.completed >= 1
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_new_conversation_preserves_approval_mode_without_reusing_history(setup):
    from app.channels.telegram import handle_inbound_text
    from app.runtime.permissions.modes import get_effective_mode

    _, api = setup
    try:
        store.update_settings({"approvals_mode": "off"})
        first = await handle_inbound_text(42, "/manual", send_reply=False)
        fresh = await handle_inbound_text(42, "/new", send_reply=False)
        assert first["session_id"] != fresh["session_id"]
        assert get_effective_mode(fresh["session_id"]) == "manual"
        assert store.get_session_history(fresh["session_id"]) == []
        assert "Manual" in fresh["reply"]
    finally:
        await api.aclose()


async def test_stop_in_same_batch_prevents_a_scheduled_turn_from_starting(
    setup, monkeypatch
):
    bot, api = setup
    llm = inject(monkeypatch, [text_reply("Must not run")])
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("run a task"))
        await dispatcher.dispatch(message("/stop"))
        await asyncio.sleep(0)
        assert not dispatcher.tasks and not dispatcher.actors
        assert llm.remaining == 1
        assert any("Stopping" in p.get("text", "") for _, p in bot.calls)
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_stop_during_status_delivery_never_starts_model(setup, monkeypatch):
    _, api = setup
    llm = inject(monkeypatch, [text_reply("Must not run")])
    entered = asyncio.Event()
    release = asyncio.Event()
    original = api.send_message

    async def delay_status(chat_id, text, **kwargs):
        if text.startswith("◌"):
            entered.set()
            await release.wait()
        return await original(chat_id, text, **kwargs)

    monkeypatch.setattr(api, "send_message", delay_status)
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("run a task"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        await dispatcher.dispatch(message("/stop"))
        release.set()
        await until(lambda: not dispatcher.tasks)
        assert llm.remaining == 1
        sid = store.find_session("main", "tg_42")
        assert not store.get_session_history(sid)
        assert not store.is_session_turn_active(sid)
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_compact_command_progress_then_edit(setup, monkeypatch):
    """/compact sends a progress message, edits it with the result, and drops
    a history marker the next turn reads instead of old messages."""
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    for i in range(5):
        store.append_session_history(
            sid, {"type": "user", "content": f"question {i}", "agent_id": "main"}
        )
        store.append_session_history(
            sid, {"type": "final", "content": f"answer {i}", "agent_id": "main"}
        )
    llm = ScriptedLLM([text_reply("Talked about five questions and answers.")])
    monkeypatch.setattr("app.runtime.llm.get_llm", lambda *a, **kw: llm)

    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("/compact"))
    finally:
        await dispatcher.close()

    # One progress message, edited in place to the result — not two messages.
    sent = [m for m in bot.messages.values()]
    assert len(sent) == 1
    assert "Compacted 10 earlier messages" in (sent[0].get("text") or "")
    history = store.get_session_history(sid)
    assert any(e.get("type") == "compact" for e in history)


async def test_compact_command_empty_session(setup, monkeypatch):
    bot, api = setup
    monkeypatch.setattr(
        "app.runtime.llm.get_llm",
        lambda *a, **kw: ScriptedLLM([text_reply("unused")]),
    )
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("/compact"))
    finally:
        await dispatcher.close()
    sent = [m.get("text") or "" for m in bot.messages.values()]
    assert any("Nothing to compact" in t for t in sent)


async def test_compact_command_via_plain_update(setup, monkeypatch):
    """process_update (webhook-style) path — /compact replies inline."""
    from app.channels.telegram import process_update

    _, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    for i in range(5):
        store.append_session_history(
            sid, {"type": "user", "content": f"q{i}", "agent_id": "main"}
        )
        store.append_session_history(
            sid, {"type": "final", "content": f"a{i}", "agent_id": "main"}
        )
    monkeypatch.setattr(
        "app.runtime.llm.get_llm",
        lambda *a, **kw: ScriptedLLM([text_reply("Summary.")]),
    )
    result = await process_update(message("/compact"), api=api)
    assert "Compacted 10 earlier messages" in result["reply"]
    await api.aclose()


async def test_colony_updates_one_status_and_waits_for_actual_answer_delivery(setup):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid)
    async def event(eid, kind, **payload):
        await ui.consume(fmt_sse({"event": "swarm.event", "data": {
            "run_id": "colony", "event_id": eid, "kind": kind, **payload}}))
    try:
        await ui.start()
        sends = sum(method == "sendMessage" for method, _ in bot.calls)
        await event(1, "run_started", coordinator_name="Tomo")
        await event(2, "task_created", task_id="a", agent_id="research", agent_name="API reviewer")
        await event(3, "task_started", task_id="a")
        await event(4, "question", task_id="a", content="Which contract?")
        assert "API reviewer: waiting for main's answer" in ui.status_text()
        assert "Waiting for your answer" not in ui.status_text()
        assert not ui.waiting
        await event(5, "message", agent_id="main", to_agent_id="research", to_task_id="a",
                    reply_to_event_id=4, content="Use v2")
        assert "waiting for main's answer" in ui.status_text()
        await event(6, "coordinator_review")
        assert "Tomo reviewing workers" in ui.status_text()
        await ui.refresh()
        assert "Tomo reviewing workers" in bot.messages[ui.status_id]["text"]
        await event(7, "question_resolved", task_id="a", question_event_id=4, status="answered")
        assert "waiting for main's answer" not in ui.status_text()
        assert "received main's answer; can continue" in ui.status_text()
        notes = list(ui.swarm_notes)
        await event(8, "coordinator_review_done", metrics={"cached_tokens": 80})
        await event(9, "coordinator_note", content="Nothing new; still waiting")
        assert ui.swarm_notes == notes
        assert "Nothing new; still waiting" not in ui.status_text()
        await ui.refresh()
        assert "Tomo reviewing workers" not in bot.messages[ui.status_id]["text"]
        assert sum(method == "sendMessage" for method, _ in bot.calls) == sends
        assert not ui.answer  # Board traffic is never promoted to the main answer.
    finally:
        await ui.close()
        await api.aclose()


async def test_colony_task_specific_timeout_replay_and_cancel_clear_waiting(setup):
    from types import SimpleNamespace
    _, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid)
    ui.consume_swarm({"kind": "run_started", "run_id": "r", "event_id": 1})
    eid = 2
    for tid in ("a", "b"):
        ui.consume_swarm({"kind": "task_created", "run_id": "r", "event_id": eid,
                          "task_id": tid, "agent_id": "research", "agent_name": f"Reviewer {tid}"})
        ui.consume_swarm({"kind": "task_started", "run_id": "r", "event_id": eid + 1, "task_id": tid})
        ui.consume_swarm({"kind": "question", "run_id": "r", "event_id": eid + 2, "task_id": tid, "content": "Need help"})
        eid += 3
    try:
        ui.consume_swarm({"kind": "question_resolved", "run_id": "r", "event_id": 8,
                          "task_id": "a", "question_event_id": 4, "status": "timeout"})
        assert "Reviewer a: main reply timed out; question unresolved" in ui.status_text()
        assert "Reviewer b: waiting for main's answer" in ui.status_text()
        before = list(ui.swarm_notes)
        ui.consume_swarm({"kind": "question", "run_id": "r", "event_id": 4, "task_id": "a"})
        assert ui.swarm_notes == before
        assert ui.swarm_tasks["a"]["question"] is None
        ui.prompts["human"] = SimpleNamespace(kind="approval")
        assert ui.status_text().startswith("◌ Waiting for approval")
        ui.prompts.clear()
        ui.consume_swarm({"kind": "run_done", "run_id": "r", "event_id": 9, "status": "cancelled"})
        assert "waiting for main's answer" not in ui.status_text()
        assert not any(t["question"] for t in ui.swarm_tasks.values())
        ui.consume_swarm({"kind": "run_started", "run_id": "next", "event_id": 10})
        assert not ui.swarm_tasks and not ui.swarm_notes
    finally:
        await api.aclose()
