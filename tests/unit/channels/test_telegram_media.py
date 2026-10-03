"""Media ingestion through real attachment history and mock Telegram transport."""

import asyncio
import copy
import json
from pathlib import Path

import httpx2
import pytest

from app.channels.telegram import TelegramAPI, TelegramDispatcher, process_update
from app.channels.telegram_media import (
    MediaError,
    ingest_media,
    transcribe_audio,
)
from app.services import store
from tests.fakes.llm import text_reply
from tests.unit.channels.test_telegram_ux import Bot, inject, message, until


def inject_recording(monkeypatch, responses):
    llm = inject(monkeypatch, responses)
    llm.calls = []
    complete = llm.complete

    async def record(messages, tools=None):
        llm.calls.append(copy.deepcopy(messages))
        return await complete(messages, tools)

    monkeypatch.setattr(llm, "complete", record)
    return llm


class MediaBot(Bot):
    def transport(self, request):
        if request.method == "GET":
            self.calls.append(("download", {}))
            return httpx2.Response(200, content=b"hello attachment")
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getFile":
            payload = json.loads(request.content)
            self.calls.append((method, payload))
            return httpx2.Response(
                200, json={"ok": True, "result": {"file_path": "documents/file.txt"}}
            )
        return super().transport(request)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store.rebind(tmp_path / "media.db")
    monkeypatch.setattr("app.core.config.TOMO_HOME", tmp_path)
    store.update_settings(
        {"telegram_allowed_chat_ids": [42, 43], "approvals_mode": "smart"}
    )
    bot = MediaBot()
    api = TelegramAPI("secret-token", transport=httpx2.MockTransport(bot.transport))
    yield bot, api


def media(kind="document", *, caption=None, chat=42, album=None, msg_id=1):
    msg = message("", chat=chat)["message"]
    msg.pop("text")
    msg["message_id"] = msg_id
    if kind == "photo":
        msg[kind] = [
            {"file_id": "small", "width": 10, "height": 10},
            {"file_id": "big", "width": 100, "height": 100},
        ]
    else:
        msg[kind] = {
            "file_id": "file",
            "file_name": "../../notes.txt",
            "mime_type": "text/plain",
        }
        if kind in {"voice", "audio"}:
            msg[kind].update(file_name="voice.ogg", mime_type="audio/ogg")
    if caption is not None:
        msg["caption"] = caption
    if album:
        msg["media_group_id"] = album
    return {"message": msg}


async def test_document_reaches_model_and_session_history(setup, monkeypatch):
    bot, api = setup
    llm = inject_recording(monkeypatch, [text_reply("I read your attachment")])
    result = await process_update(media(caption="Read this"), api=api)
    history = store.get_session_history(result["session_id"])
    user = next(e for e in history if e["type"] == "user")
    assert user["content"] == "Read this"
    assert len(user["attachment_ids"]) == 1
    att = store.get_attachment(user["attachment_ids"][0])
    assert att["original_name"] == "notes.txt"
    assert Path(att["file_path"]).read_bytes() == b"hello attachment"
    assert "hello attachment" in str(llm.calls)
    assert any(m == "download" for m, _ in bot.calls)
    await api.aclose()


async def test_photo_chooses_largest_resolution_and_caption_is_not_command(
    setup, monkeypatch
):
    bot, api = setup
    inject(monkeypatch, [text_reply("Image received")])
    await process_update(media("photo", caption="/new"), api=api)
    assert next(p for m, p in bot.calls if m == "getFile")["file_id"] == "big"
    sid = store.find_session("main", "tg_42")
    assert any(e.get("content") == "/new" for e in store.get_session_history(sid))
    assert len(store.list_session_attachments(sid)) == 1
    await api.aclose()


async def test_unapproved_media_never_downloaded_or_creates_session(setup):
    bot, api = setup
    await process_update(media("voice", chat=999), api=api)
    assert not any(m in {"getFile", "download"} for m, _ in bot.calls)
    assert store.find_session("main", "tg_999") is None
    await api.aclose()


async def test_disabled_transcription_saves_audio_without_model_turn(
    setup, monkeypatch
):
    _, api = setup
    llm = inject_recording(monkeypatch, [])
    await process_update(media("voice"), api=api)
    sid = store.find_session("main", "tg_42")
    user = store.get_session_history(sid)[0]
    assert user["attachment_ids"]
    assert llm.calls == []
    await api.aclose()


async def test_voice_transcript_runs_in_same_session_and_retains_audio(
    setup, monkeypatch
):
    _, api = setup

    async def transcript(*args):
        return "Summarize my notes in Indonesian"

    monkeypatch.setattr("app.channels.telegram_media.transcribe_audio", transcript)
    llm = inject_recording(monkeypatch, [text_reply("Ringkasan")])
    result = await process_update(media("voice"), api=api)
    user = next(
        e
        for e in store.get_session_history(result["session_id"])
        if e["type"] == "user"
    )
    assert "Summarize my notes" in user["content"]
    assert user["attachment_ids"]
    assert "Summarize my notes" in str(llm.calls)
    await api.aclose()


async def test_album_is_one_turn_and_other_chat_remains_responsive(setup, monkeypatch):
    bot, api = setup
    inject(monkeypatch, [text_reply("Other chat"), text_reply("Album")])
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(media("photo", caption="Compare", album="A"))
        await dispatcher.dispatch(media("photo", album="A", msg_id=2))
        await dispatcher.dispatch(media("photo", album="A", msg_id=2))
        await dispatcher.dispatch(message("hello", chat=43))
        await until(lambda: 43 not in dispatcher.tasks)
        assert 42 in dispatcher.tasks
        await until(lambda: not dispatcher.tasks)
        sid = store.find_session("main", "tg_42")
        users = [e for e in store.get_session_history(sid) if e["type"] == "user"]
        assert len(users) == 1
        assert len(users[0]["attachment_ids"]) == 2
        assert len([m for m, _ in bot.calls if m == "getFile"]) == 2
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_size_rejected_before_download(setup):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    update = media()
    update["message"]["document"]["file_size"] = 21 * 1024 * 1024
    with pytest.raises(MediaError, match="too large"):
        await ingest_media(api, sid, update["message"])
    assert not bot.calls
    assert not store.list_session_attachments(sid)
    await api.aclose()


async def test_revocation_after_download_prevents_storage_and_transcription(setup):
    _, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    with pytest.raises(asyncio.CancelledError):
        await ingest_media(api, sid, media("voice")["message"], allowed=lambda: False)
    assert store.list_session_attachments(sid) == []
    await api.aclose()


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/tmp/secret",
        "https://other/file",
        "files/%2e%2e/key",
        "file?token=secret",
        "file#fragment",
    ],
)
async def test_invalid_telegram_download_paths_rejected(path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(200, json={"ok": True, "result": {"file_path": path}})

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    with pytest.raises(MediaError, match="valid file"):
        await api.download_file("f", max_bytes=20)
    assert len(calls) == 1
    await api.aclose()


async def test_streamed_size_limit_and_network_error_do_not_expose_token():
    def handler(request):
        if request.method == "POST":
            return httpx2.Response(
                200, json={"ok": True, "result": {"file_path": "files/a"}}
            )
        return httpx2.Response(200, content=b"x" * 21)

    api = TelegramAPI("secret", transport=httpx2.MockTransport(handler))
    with pytest.raises(MediaError, match="too large"):
        await api.download_file("a", max_bytes=20)
    await api.aclose()

    def fail(request):
        if request.method == "POST":
            return handler(request)
        raise httpx2.ReadError(str(request.url))

    api = TelegramAPI("super-secret", transport=httpx2.MockTransport(fail))
    with pytest.raises(MediaError) as err:
        await api.download_file("a", max_bytes=20)
    assert "super-secret" not in str(err.value)
    assert err.value.__cause__ is None
    await api.aclose()


async def test_transcription_multipart_and_safe_failure(setup):
    store.update_settings(
        {
            "telegram_transcription_enabled": True,
            "telegram_transcription_api_key": "private-stt-key",
            "telegram_transcription_base_url": "https://stt.example/v1",
            "telegram_transcription_model": "speech-model",
        }
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(200, json={"text": "Halo Tomo"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        assert (
            await transcribe_audio(b"audio", "voice.ogg", "audio/ogg", client=client)
            == "Halo Tomo"
        )
    assert str(calls[0].url) == "https://stt.example/v1/audio/transcriptions"
    assert calls[0].headers["authorization"] == "Bearer private-stt-key"
    assert b"speech-model" in calls[0].content and b"voice.ogg" in calls[0].content

    def fail(request):
        raise httpx2.ReadError("private-stt-key")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as client:
        with pytest.raises(MediaError) as err:
            await transcribe_audio(b"audio", "voice.ogg", "audio/ogg", client=client)
    assert "private-stt-key" not in str(err.value)




async def test_received_rich_media_blocks_share_one_turn(setup, monkeypatch):
    _, api = setup
    inject(monkeypatch, [text_reply("Both files received")])
    update = message("")
    update["message"].pop("text")
    update["message"]["rich_message"] = {
        "blocks": [
            {
                "type": "paragraph",
                "text": ["Compare ", {"type": "bold", "text": "these files"}],
            },
            {
                "type": "collage",
                "blocks": [
                    {
                        "type": "document",
                        "document": {
                            "file_id": "d1",
                            "file_name": "a.txt",
                            "mime_type": "text/plain",
                        },
                    },
                    {
                        "type": "document",
                        "document": {
                            "file_id": "d2",
                            "file_name": "b.txt",
                            "mime_type": "text/plain",
                        },
                    },
                ],
            },
        ]
    }
    result = await process_update(update, api=api)
    users = [
        e
        for e in store.get_session_history(result["session_id"])
        if e["type"] == "user"
    ]
    assert len(users) == 1 and len(users[0]["attachment_ids"]) == 2
    assert "Compare these files" in users[0]["content"]
    await api.aclose()


async def test_media_process_send_reply_false_preserves_history_without_sends(
    setup, monkeypatch
):
    bot, api = setup
    inject(monkeypatch, [text_reply("Received")])
    result = await process_update(media(caption="read"), api=api, send_reply=False)
    assert result["reply"] == "Received"
    assert [m for m, _ in bot.calls] == ["getFile", "download"]
    await api.aclose()


async def test_stop_during_album_collection_prevents_download_and_agent(
    setup, monkeypatch
):
    bot, api = setup
    llm = inject_recording(monkeypatch, [])
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(media("photo", album="A"))
        await until(lambda: 42 in dispatcher.uis)
        await dispatcher.dispatch(message("/stop"))
        await until(lambda: not dispatcher.tasks)
        assert not any(m == "getFile" for m, _ in bot.calls)
        assert not llm.calls
        assert not dispatcher.albums
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_stop_interrupts_active_download_and_never_calls_agent(
    setup, monkeypatch
):
    bot, api = setup
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def download(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(api, "download_file", download)
    llm = inject_recording(monkeypatch, [])
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(media("voice"))
        await asyncio.wait_for(started.wait(), 2)
        await dispatcher.dispatch(message("/stop"))
        await until(lambda: not dispatcher.tasks)
        assert cancelled.is_set()
        assert not llm.calls
        sid = store.find_session("main", "tg_42")
        assert not store.list_session_attachments(sid)
        assert any("Stopped" in p.get("text", "") for _, p in bot.calls)
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_received_photo_pixels_reach_vision_model(setup, monkeypatch):
    _, api = setup
    monkeypatch.setattr(
        "app.runtime.llm.vision.decide_image_input_mode", lambda agent_id: "native"
    )
    llm = inject_recording(monkeypatch, [text_reply("Image received")])
    await process_update(media("photo", caption="Describe this image"), api=api)
    parts = [
        part
        for call in llm.calls
        for item in call
        if isinstance(item.get("content"), list)
        for part in item["content"]
    ]
    images = [part for part in parts if part.get("type") == "image_url"]
    assert images and images[0]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    await api.aclose()
