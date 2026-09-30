"""Bot API failure behavior: no duplicate sends on transport errors, bounded floods."""

import json

import httpx
import pytest

from app.channels.telegram import TelegramAPI, TelegramAPIError


async def test_entity_error_falls_back_to_plain_text_and_keeps_buttons():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("parse_mode"):
            return httpx.Response(
                400,
                json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: can't parse entities",
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    api = TelegramAPI("secret-token", transport=httpx.MockTransport(handler))
    try:
        keyboard = {
            "inline_keyboard": [[{"text": "Once", "callback_data": "ta:test:once"}]]
        }
        await api.send_message(
            42, "**bold** & <literal>", formatted=True, reply_markup=keyboard
        )
        assert len(calls) == 2
        assert calls[1]["text"] == "bold & <literal>"
        assert "parse_mode" not in calls[1]
        assert calls[1]["reply_markup"] == keyboard
    finally:
        await api.aclose()


async def test_flood_retry_respects_server_delay_and_is_bounded(monkeypatch):
    attempts = []
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    def handler(request):
        attempts.append(request)
        return httpx.Response(
            429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 2}}
        )

    monkeypatch.setattr("app.channels.telegram.asyncio.sleep", sleep)
    api = TelegramAPI("secret-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(TelegramAPIError):
            await api.send_message(42, "hello")
        assert len(attempts) == 3
        assert sleeps == [2, 2]
    finally:
        await api.aclose()


async def test_typing_flood_is_not_retried_or_allowed_to_block_status(monkeypatch):
    calls = []

    async def sleep(delay):
        pytest.fail("Typing backoff belongs in the UI ticker")

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429,
            json={"ok": False, "error_code": 429, "parameters": {"retry_after": 30}},
        )

    monkeypatch.setattr("app.channels.telegram.asyncio.sleep", sleep)
    api = TelegramAPI("secret-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(TelegramAPIError):
            await api.send_typing(42)
        assert len(calls) == 1
    finally:
        await api.aclose()


async def test_transport_error_is_redacted_and_not_retried():
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ConnectError("secret-token in a token-bearing URL", request=request)

    api = TelegramAPI("secret-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError) as error:
            await api.send_message(42, "hello")
        assert "secret-token" not in str(error.value)
        assert len(attempts) == 1
    finally:
        await api.aclose()


async def test_not_modified_edit_is_successful_noop():
    def handler(request):
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message is not modified",
            },
        )

    api = TelegramAPI("secret-token", transport=httpx.MockTransport(handler))
    try:
        assert await api.edit_message(42, 1, "same") == {}
    finally:
        await api.aclose()


@pytest.mark.parametrize("same_bot", [True, False])
async def test_supervisor_restores_only_this_bots_admission_cursor(
    tmp_path, monkeypatch, same_bot
):
    import asyncio
    import hashlib

    from app.channels.telegram import _supervisor_loop
    from app.services import store

    store.rebind(tmp_path / "cursor.db")
    token = "cursor-test-token"
    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    store.update_settings(
        {
            "telegram_bot_token": token,
            "telegram_enabled": True,
            "telegram_update_cursor": {
                "bot": fingerprint if same_bot else "other-bot",
                "offset": 70,
            },
        }
    )
    stop = asyncio.Event()
    polls = []

    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/getUpdates"):
            polls.append(body.get("offset"))
            if len(polls) == 1:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": [
                            {
                                "update_id": 70,
                                "message": {
                                    "chat": {"id": 42},
                                    "from": {"id": 42},
                                    "text": "/id",
                                },
                            }
                        ],
                    },
                )
            stop.set()
            result = []
        else:
            result = {}
        return httpx.Response(200, json={"ok": True, "result": result})

    api = TelegramAPI(token, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("app.channels.telegram.TelegramAPI", lambda token: api)
    await _supervisor_loop(stop)
    assert polls == [70 if same_bot else None, 71]
    assert store.get_settings()["telegram_update_cursor"] == {
        "bot": fingerprint,
        "offset": 71,
    }
    assert store.get_settings()["telegram_bot_token"] == token
    store.update_settings({"telegram_enabled": False})
