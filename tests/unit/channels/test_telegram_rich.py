"""Native rich messages, streaming drafts, and conservative compatibility fallback."""

import json

import httpx
import pytest

from app.channels.telegram import (
    TelegramAPI,
    TelegramAPIError,
    TelegramDispatcher,
    extract_text_message,
)
from app.channels.telegram_format import render_rich_html
from app.channels.telegram_ui import TelegramTurnUI
from app.services import store
from tests.fakes.llm import text_reply
from tests.unit.channels.test_telegram_ux import Bot, inject, message, until


@pytest.fixture
def settings(tmp_path):
    store.rebind(tmp_path / "rich.db")
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
        calls.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    api = TelegramAPI("secret", transport=httpx.MockTransport(handler))
    await api.send_answer(42, "# Native", thread_id=9, reply_to=7)
    await api.edit_answer(42, 1, "| A | B |\n|---|---|\n| 1 | 2 |")
    assert await api.send_rich_draft(42, 123, "partial", thread_id=9)
    assert [m for m, _ in calls] == [
        "sendRichMessage",
        "editMessageText",
        "sendRichMessageDraft",
    ]
    assert calls[0][1]["message_thread_id"] == 9
    assert calls[0][1]["reply_parameters"]["message_id"] == 7
    assert "<h1>" in calls[0][1]["rich_message"]["html"]
    assert "<table>" in calls[1][1]["rich_message"]["html"]
    assert "text" not in calls[1][1]
    assert calls[2][1]["draft_id"] == 123
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
            return httpx.Response(
                code,
                json={
                    "ok": False,
                    "error_code": code,
                    "description": "unsupported format",
                },
            )
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": len(calls)}}
        )

    api = TelegramAPI("secret", transport=httpx.MockTransport(handler))
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
            raise httpx.ReadError(str(request.url))
        return httpx.Response(
            failure,
            json={
                "ok": False,
                "error_code": failure,
                "parameters": {"retry_after": 99},
            },
        )

    api = TelegramAPI("secret", transport=httpx.MockTransport(handler))
    with pytest.raises((RuntimeError, TelegramAPIError)):
        await api.send_answer(42, "answer")
    assert len(calls) == 1
    await api.aclose()


async def test_draft_unavailable_does_not_disable_final_rich_delivery(settings):
    calls = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "sendRichMessageDraft":
            return httpx.Response(404, json={"ok": False, "error_code": 404})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    api = TelegramAPI("secret", transport=httpx.MockTransport(handler))
    assert not await api.send_rich_draft(42, 123, "draft")
    assert api.rich_enabled
    assert not await api.send_rich_draft(42, 123, "another draft")
    await api.send_answer(42, "final")
    assert calls == ["sendRichMessageDraft", "sendRichMessage"]
    await api.aclose()


class RichBot(Bot):
    def transport(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        if method in {"sendRichMessage", "sendRichMessageDraft"}:
            payload = json.loads(request.content)
            self.calls.append((method, payload))
            if method == "sendRichMessage":
                self.counter += 1
                result = {"message_id": self.counter, **payload}
                self.messages[self.counter] = result
            else:
                result = True
            return httpx.Response(200, json={"ok": True, "result": result})
        return super().transport(request)


async def test_private_draft_is_persisted_once_and_keeps_approval_status_card(settings):
    bot = RichBot()
    api = TelegramAPI("secret", transport=httpx.MockTransport(bot.transport))
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid, actor_id=42)
    ui.answer = "# Preview\n\nText"
    await ui.start()
    try:
        await ui.refresh()
        await ui.refresh()
        await ui.finish("# Final\n\nComplete answer")
        drafts = [p for m, p in bot.calls if m == "sendRichMessageDraft"]
        assert drafts
        assert len({p["draft_id"] for p in drafts}) == 1
        assert ui.answer_id is None
        assert len([m for m, _ in bot.calls if m == "sendRichMessage"]) == 1
        assert bot.messages[ui.status_id]["reply_markup"]["inline_keyboard"] == []
    finally:
        await ui.close()
        await api.aclose()


async def test_group_rich_preview_edited_in_place_without_private_draft(settings):
    bot = RichBot()
    api = TelegramAPI("secret", transport=httpx.MockTransport(bot.transport))
    sid = store.get_or_create_session("main", "tg_-100")
    ui = TelegramTurnUI(api, -100, sid, actor_id=42, thread_id=8)
    ui.answer = "# Preview"
    await ui.refresh()
    mid = ui.answer_id
    await ui.finish("# Final")
    assert not any(m == "sendRichMessageDraft" for m, _ in bot.calls)
    assert len([m for m, _ in bot.calls if m == "sendRichMessage"]) == 1
    assert "<h1>Final</h1>" in bot.messages[mid]["rich_message"]["html"]
    await ui.close()
    await api.aclose()


async def test_inbound_rich_text_reaches_real_turn(settings, monkeypatch):
    bot = RichBot()
    api = TelegramAPI("secret", transport=httpx.MockTransport(bot.transport))
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
            method in {"sendRichMessage", "sendRichMessageDraft"}
            or "rich_message" in payload
        ):
            return httpx.Response(
                400,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "unsupported format",
                },
            )
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": len(calls)}}
        )

    api = TelegramAPI("secret", transport=httpx.MockTransport(handler))
    sid = store.get_or_create_session("main", "tg_42")
    ui = TelegramTurnUI(api, 42, sid, actor_id=42)
    ui.answer = "🚀" * 4000
    await ui.refresh()
    assert len([m for m, _ in calls if m == "sendMessage"]) == 1
    await ui.finish("🚀" * 4000 + "THE END")
    # One preview is replaced; remaining answer chunks arrive once each.
    edited = [p for m, p in calls if m == "editMessageText" and "text" in p][-1]
    overflow = [p for m, p in calls if m == "sendMessage"][1:]
    assert (
        edited["text"] + "".join(p["text"] for p in overflow) == "🚀" * 4000 + "THE END"
    )
    await ui.close()
    await api.aclose()
