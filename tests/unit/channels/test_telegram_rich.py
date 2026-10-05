"""Native rich messages, streaming drafts, and conservative compatibility fallback."""

import asyncio
import json

import httpx2
import pytest

from app.channels.telegram import (
    TelegramAPI,
    TelegramAPIError,
    TelegramDispatcher,
    extract_text_message,
)
from app.channels.telegram_format import render_rich_html, utf16_len
from app.channels.telegram_ui import TelegramTurnUI
from app.channels.sse_map import fmt_sse
from app.services import store
from tests.fakes.llm import text_reply
from tests.unit.channels.test_telegram_ux import Bot, inject, message, until


@pytest.fixture
def settings(tmp_path):
    store.rebind(tmp_path / "rich.db")
    from tests.fakes.access import seed_telegram_accounts
    seed_telegram_accounts([42, -100])
    store.update_settings(
        {
            "telegram_rich_messages": True,
            "telegram_allowed_chat_ids": [42, -100],
            "approvals_mode": "smart",
        }
    )


def test_rich_renderer_preserves_structure_and_escapes_untrusted_controls():
    rendered = render_rich_html(
        '# Heading\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- first\n- second\n\n```math\nx^2\n```\n\n<tg-button data="grant">Grant</tg-button>\n\n![private](https://private.example/image.png)'
    )
    assert "<h1>Heading</h1>" in rendered
    assert "<table>" in rendered and "<th>A</th>" in rendered
    assert "<ul>" in rendered
    assert "<tg-math-block>x^2" in rendered
    assert "<tg-button" not in rendered
    assert "<img" not in rendered and "https://private.example" not in rendered


async def test_rich_send_edit_and_draft_preserve_routing(settings):
    calls = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append((method, json.loads(request.content)))
        result = True if method == "sendMessageDraft" else {"message_id": 1}
        return httpx2.Response(200, json={"ok": True, "result": result})

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    await api.send_answer(42, "# Native", thread_id=9, reply_to=7)
    await api.edit_answer(42, 1, "| A | B |\n|---|---|\n| 1 | 2 |")
    assert await api.send_draft(42, 123, "partial", thread_id=9)
    assert [m for m, _ in calls] == [
        "sendRichMessage",
        "editMessageText",
        "sendMessageDraft",
    ]
    assert calls[0][1]["message_thread_id"] == 9
    assert calls[0][1]["reply_parameters"]["message_id"] == 7
    assert "<h1>" in calls[0][1]["rich_message"]["html"]
    assert "<table>" in calls[1][1]["rich_message"]["html"]
    assert "text" not in calls[1][1]
    assert calls[2][1] == {
        "chat_id": 42,
        "draft_id": 123,
        "text": "partial",
        "message_thread_id": 9,
    }
    await api.aclose()


@pytest.mark.parametrize("code", [400, 404, 501])
async def test_definite_rejection_falls_back_and_long_answers_are_complete(
    settings, code
):
    calls = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        calls.append((method, payload))
        if method == "sendRichMessage":
            return httpx2.Response(
                code,
                json={
                    "ok": False,
                    "error_code": code,
                    "description": "unsupported format",
                },
            )
        return httpx2.Response(
            200, json={"ok": True, "result": {"message_id": len(calls)}}
        )

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    answer = "long answer " * 1000 + "THE END"
    await api.send_answer(42, answer, thread_id=8, reply_to=7)
    sent = [p for m, p in calls if m == "sendMessage"]
    assert len(sent) > 1
    assert "".join(p["text"] for p in sent) == answer
    assert sent[0]["reply_parameters"]["message_id"] == 7
    assert all(p["message_thread_id"] == 8 for p in sent)
    if code in {404, 501}:
        assert not api.rich_enabled
    else:
        assert api.rich_enabled
    await api.aclose()


@pytest.mark.parametrize("failure", ["network", 403, 500, 429])
async def test_uncertain_or_permission_failure_never_sends_legacy_duplicate(
    settings, failure
):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "network":
            raise httpx2.ReadError(str(request.url))
        return httpx2.Response(
            failure,
            json={
                "ok": False,
                "error_code": failure,
                "parameters": {"retry_after": 99},
            },
        )

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    with pytest.raises((RuntimeError, TelegramAPIError)):
        await api.send_answer(42, "answer")
    assert len(calls) == 1
    await api.aclose()


async def test_draft_unavailable_does_not_disable_final_rich_delivery(settings):
    calls = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "sendMessageDraft":
            return httpx2.Response(404, json={"ok": False, "error_code": 404})
        return httpx2.Response(200, json={"ok": True, "result": {"message_id": 1}})

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    assert not await api.send_draft(42, 123, "draft")
    assert api.rich_enabled
    assert not await api.send_draft(42, 123, "another draft")
    await api.send_answer(42, "final")
    assert calls == ["sendMessageDraft", "sendRichMessage"]
    await api.aclose()


class RichBot(Bot):
    def __init__(self):
        super().__init__()
        self.drafts = {}

    def transport(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        if method in {"sendRichMessage", "sendMessageDraft"}:
            payload = json.loads(request.content)
            self.calls.append((method, payload))
            if method == "sendRichMessage":
                self.counter += 1
                result = {"message_id": self.counter, **payload}
                self.messages[self.counter] = result
                self.drafts.pop(payload["chat_id"], None)
            else:
                self.drafts[payload["chat_id"]] = payload
                result = True
            return httpx2.Response(200, json={"ok": True, "result": result})
        response = super().transport(request)
        if method == "sendMessage":
            self.drafts.pop(json.loads(request.content)["chat_id"], None)
        return response


@pytest.mark.parametrize("rich", [False, True])
async def test_tool_commentary_shown_once_and_final_is_newest(settings, rich):
    store.update_settings({"telegram_rich_messages": rich})
    bot = RichBot()
    api = TelegramAPI("secret", transport=httpx2.MockTransport(bot.transport))
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid, reply_to=7, thread_id=8)

    def visible_texts():
        return [
            p.get("text") or p["rich_message"]["html"]
            for _, p in sorted(bot.messages.items())
        ]

    try:
        for content in ["Checking tunnel.", "Checking cameras."]:
            await ui.consume(fmt_sse({"event": "delta", "data": {"content": content}}))
            await ui.refresh()
            # Each model round has an ephemeral draft, not a permanent preview.
            assert bot.drafts[42]["text"] == content
            assert not any(content in text for text in visible_texts())
            await ui.consume(
                fmt_sse({"event": "assistant_progress", "data": {"content": content}})
            )
            assert sum(content in text for text in visible_texts()) == 1
            assert "…" not in visible_texts()[-1]
        await ui.consume(fmt_sse({"event": "delta", "data": {"content": "All done."}}))
        await ui.refresh()
        assert bot.drafts[42]["text"] == "All done."
        drafts = [p for m, p in bot.calls if m == "sendMessageDraft"]
        assert len({p["draft_id"] for p in drafts}) == 3
        # Another channel message must not strand the final above it.
        await api.send_message(42, "Guidance received.", thread_id=8, silent=True)
        await ui.finish("All done.")
        assert not bot.drafts
        assert "All done." in visible_texts()[-1]
        assert sum("All done." in text for text in visible_texts()) == 1
        final = bot.messages[max(bot.messages)]
        assert final["message_thread_id"] == 8
        assert final["reply_parameters"]["message_id"] == 7
        assert not any(m == "sendRichMessageDraft" for m, _ in bot.calls)
    finally:
        await ui.close()
        await api.aclose()


async def test_failed_final_delivery_keeps_streaming_preview(settings):
    bot = RichBot()
    fail_final = False

    def handler(request):
        if fail_final and request.url.path.endswith("/sendRichMessage"):
            return httpx2.Response(503, json={"ok": False, "error_code": 503})
        return bot.transport(request)

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    sid = store.get_or_create_session("main", "tg_-100")
    ui = TelegramTurnUI(api, -100, sid)
    try:
        await ui.start()
        status_id = ui.status_id
        ui.answer = "Preview"
        await ui.refresh()
        preview_id = max(bot.messages)
        fail_final = True
        with pytest.raises(TelegramAPIError):
            await ui.finish("Final")
        await ui.close()
        assert "Preview" in bot.messages[preview_id]["rich_message"]["html"]
        assert status_id in bot.messages
        assert not any(m == "deleteMessage" for m, _ in bot.calls)
    finally:
        await ui.close()
        await api.aclose()


@pytest.mark.parametrize("rich", [False, True])
async def test_finish_waits_for_accepted_preview_before_sending_final(settings, rich):
    store.update_settings({"telegram_rich_messages": rich})
    bot = RichBot()
    accepted = asyncio.Event()
    release = asyncio.Event()
    reply = "Connector updated."

    async def handler(request):
        response = bot.transport(request)
        payload = json.loads(request.content)
        text = payload.get("text") or payload.get("rich_message", {}).get("html", "")
        if (
            request.url.path.rsplit("/", 1)[-1] in {"sendMessage", "sendRichMessage"}
            and "…" in text
        ):
            accepted.set()
            await release.wait()
        return response

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    sid = store.get_or_create_session("main", "tg_-100")
    ui = TelegramTurnUI(api, -100, sid, reply_to=7, thread_id=8)
    ui.answer = reply
    finish_task = None
    try:
        await ui.start()
        await asyncio.wait_for(accepted.wait(), 2)
        finish_task = asyncio.create_task(ui.finish(reply))
        await until(lambda: ui.finished)
        release.set()
        await asyncio.wait_for(finish_task, 2)
        await ui.refresh()  # A late refresh must not recreate the preview.
        await ui.close()
        assert len(bot.messages) == 1
        final = next(iter(bot.messages.values()))
        assert reply in (final.get("text") or final["rich_message"]["html"])
        assert final["reply_parameters"]["message_id"] == 7
        assert final["message_thread_id"] == 8
    finally:
        release.set()
        if finish_task and not finish_task.done():
            await finish_task
        await ui.close()
        await api.aclose()


async def test_failed_preview_delete_removes_duplicate_text_and_retries_on_close(
    settings,
):
    bot = RichBot()
    fail_delete = True

    def handler(request):
        if fail_delete and request.url.path.endswith("/deleteMessage"):
            return httpx2.Response(503, json={"ok": False, "error_code": 503})
        return bot.transport(request)

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    sid = store.get_or_create_session("main", "tg_-100")
    ui = TelegramTurnUI(api, -100, sid)
    ui.answer = "Connector updated."
    try:
        await ui.start()
        await ui.refresh()
        await ui.finish("Connector updated. Complete.")
        answers = [
            p["rich_message"]["html"]
            for p in bot.messages.values()
            if "rich_message" in p
        ]
        assert sum("Connector updated." in text for text in answers) == 1
        assert any("Response delivered below." in text for text in answers)
        fail_delete = False
        await ui.close()
        assert len(bot.messages) == 1
        assert "Complete." in next(iter(bot.messages.values()))["rich_message"]["html"]
    finally:
        fail_delete = False
        await ui.close()
        await api.aclose()


async def test_private_plain_draft_streams_then_persists_one_complete_rich_answer(
    settings,
):
    bot = RichBot()
    api = TelegramAPI("secret", transport=httpx2.MockTransport(bot.transport))
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid, reply_to=7, thread_id=8)
    ui.answer = "# Preview\n\nText"
    await ui.start()
    status_id = ui.status_id
    try:
        await ui.refresh()
        await ui.refresh()
        assert list(bot.messages) == [status_id]
        assert bot.drafts[42]["text"] == ui.answer
        ui.answer = "🚀" * 2500 + "\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nTHE END"
        await ui.refresh()
        drafts = [p for m, p in bot.calls if m == "sendMessageDraft"]
        assert len(drafts) == 2
        assert drafts[0]["draft_id"] == drafts[1]["draft_id"] != 0
        assert all(p["message_thread_id"] == 8 for p in drafts)
        assert utf16_len(drafts[-1]["text"]) <= 4096
        assert not any("rich_message" in p or "parse_mode" in p for p in drafts)
        await ui.finish(ui.answer)
        await ui.refresh()
        await ui.close()
        assert not bot.drafts
        assert not any(m == "sendRichMessageDraft" for m, _ in bot.calls)
        assert len([m for m, _ in bot.calls if m == "sendRichMessage"]) == 1
        assert len(bot.messages) == 1
        final = next(iter(bot.messages.values()))
        rendered = final["rich_message"]["html"]
        assert rendered.count("🚀") == 2500
        assert "<table>" in rendered and "THE END" in rendered
        assert final["reply_parameters"]["message_id"] == 7
        assert final["message_thread_id"] == 8
        assert status_id not in bot.messages
    finally:
        await ui.close()
        await api.aclose()


@pytest.mark.parametrize("failure", [400, "network"])
async def test_private_draft_failure_falls_back_once_without_losing_rich_final(
    settings, failure
):
    bot = RichBot()

    def handler(request):
        if request.url.path.endswith("/sendMessageDraft"):
            bot.calls.append(("sendMessageDraft", json.loads(request.content)))
            if failure == "network":
                raise httpx2.ReadError("draft connection lost")
            return httpx2.Response(400, json={"ok": False, "error_code": 400})
        return bot.transport(request)

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid, reply_to=7, thread_id=8)
    try:
        ui.answer = "Checking."
        await ui.refresh()
        ui.answer = "Checking. Nearly done."
        await ui.refresh()
        assert len(bot.messages) == 1
        await ui.finish("# Done\n\n| A | B |\n|---|---|\n| 1 | 2 |")
        await ui.close()
        assert len([m for m, _ in bot.calls if m == "sendMessageDraft"]) == 1
        assert len(bot.messages) == 1
        final = next(iter(bot.messages.values()))
        assert "<table>" in final["rich_message"]["html"]
        assert final["reply_parameters"]["message_id"] == 7
        assert final["message_thread_id"] == 8
    finally:
        await ui.close()
        await api.aclose()


async def test_group_rich_final_sent_after_preview_without_private_draft(settings):
    bot = RichBot()
    api = TelegramAPI("secret", transport=httpx2.MockTransport(bot.transport))
    sid = store.get_or_create_session("main", "tg_-100")
    ui = TelegramTurnUI(api, -100, sid, actor_id=42, thread_id=8)
    ui.answer = "# Preview"
    await ui.refresh()
    mid = ui.answer_id
    await ui.finish("# Final")
    assert not any(
        m in {"sendMessageDraft", "sendRichMessageDraft"} for m, _ in bot.calls
    )
    assert len([m for m, _ in bot.calls if m == "sendRichMessage"]) == 2
    assert mid not in bot.messages
    final = bot.messages[max(bot.messages)]
    assert "<h1>Final</h1>" in final["rich_message"]["html"]
    assert final["message_thread_id"] == 8
    await ui.close()
    await api.aclose()


async def test_inbound_rich_text_reaches_real_turn(settings, monkeypatch):
    bot = RichBot()
    api = TelegramAPI("secret", transport=httpx2.MockTransport(bot.transport))
    inject(monkeypatch, [text_reply("Received rich text")])
    update = message("")
    update["message"].pop("text")
    update["message"]["rich_message"] = {
        "blocks": [
            {"type": "paragraph", "text": {"type": "concat", "texts": []}},
            {"type": "paragraph", "text": {"type": "bold", "text": "Hello Tomo"}},
        ]
    }
    assert extract_text_message(update) == (42, "Hello Tomo") or extract_text_message(
        update
    ) == (42, "\nHello Tomo".strip())
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(update)
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        assert any(
            "Hello Tomo" in e.get("content", "") for e in store.get_session_history(sid)
        )
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_long_rich_preview_rejection_keeps_single_preview_then_complete_final(
    settings,
):
    calls = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        calls.append((method, payload))
        if (
            method in {"sendRichMessage", "sendMessageDraft"}
            or "rich_message" in payload
        ):
            return httpx2.Response(
                400,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "unsupported format",
                },
            )
        return httpx2.Response(
            200, json={"ok": True, "result": {"message_id": len(calls)}}
        )

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid, actor_id=42)
    ui.answer = "🚀" * 4000
    await ui.refresh()
    assert len([m for m, _ in calls if m == "sendMessage"]) == 1
    await ui.finish("🚀" * 4000 + "THE END")
    # Final chunks arrive once each, then the temporary preview is removed.
    final = [p for m, p in calls if m == "sendMessage"][1:]
    assert "".join(p["text"] for p in final) == "🚀" * 4000 + "THE END"
    assert calls[-1][0] == "deleteMessage"
    await ui.close()
    await api.aclose()
