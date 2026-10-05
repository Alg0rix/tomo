"""Channel context and outbound artifacts through the real turn/transport seam."""

import copy
import json
from email import policy
from email.parser import BytesParser

import httpx2
import pytest

from app.channels.telegram import TelegramAPI, TelegramDispatcher, run_channel_turn
from app.core import home
from app.runtime.artifacts.fs import artifacts_dir, write_artifact_text
from app.runtime.llm.base import LLMResponse, ToolCall
from app.services import store
from tests.fakes.llm import ScriptedLLM, text_reply
from tests.unit.channels.test_telegram_ux import Bot, message, until


class UploadBot(Bot):
    def __init__(self):
        super().__init__()
        self.uploads = []
        self.reject_uploads = False

    def transport(self, request):
        method = request.url.path.rsplit("/", 1)[-1]
        if method not in {"sendPhoto", "sendDocument"}:
            return super().transport(request)
        multipart = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + request.headers["content-type"].encode()
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + request.content
        )
        parts = {
            part.get_param("name", header="content-disposition"): part
            for part in multipart.iter_parts()
        }
        self.uploads.append((method, parts))
        if self.reject_uploads:
            return httpx2.Response(503, json={"ok": False, "error_code": 503})
        self.counter += 1
        return httpx2.Response(
            200, json={"ok": True, "result": {"message_id": self.counter}}
        )


class RecordingLLM(ScriptedLLM):
    def __init__(self, responses):
        super().__init__(responses)
        self.calls = []

    async def complete(self, messages, tools=None):
        self.calls.append(copy.deepcopy((messages, tools)))
        return await super().complete(messages, tools)


def calls(*items):
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(id=str(i), name=name, arguments=args)
            for i, (name, args) in enumerate(items)
        ],
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store.rebind(tmp_path / "delivery.db")
    monkeypatch.setattr("app.core.config.TOMO_HOME", tmp_path / "home")
    monkeypatch.setattr("app.core.config.TOMO_WORK", tmp_path / "work")
    store.update_settings(
        {
            "telegram_allowed_chat_ids": [42, -100],
            "approvals_mode": "smart",
            "learning_enabled": False,
        }
    )
    bot = UploadBot()
    api = TelegramAPI("private-bot-token", transport=httpx2.MockTransport(bot.transport))
    yield bot, api


def install(monkeypatch, responses):
    llm = RecordingLLM(responses)
    monkeypatch.setattr("app.runtime.agent.loop.get_llm", lambda agent_id=None, **kwargs: llm)
    return llm


async def test_telegram_context_delivers_photo_and_document_without_leaking_to_web(
    setup, monkeypatch
):
    bot, api = setup
    root = home.agent_work_dir("main")
    root.mkdir(parents=True, exist_ok=True)
    image = b"\x89PNG\r\n\x1a\nimage-bytes"
    (root / "shot.png").write_bytes(image)
    llm = install(
        monkeypatch,
        [
            calls(
                ("save_artifact", {"filename": "shot.png", "source_path": "shot.png"}),
                ("save_artifact", {"filename": "report.md", "content": "# Report"}),
            ),
            calls(
                (
                    "telegram_send_file",
                    {"filename": "shot.png", "caption": "Screenshot"},
                ),
                ("telegram_send_file", {"filename": "report.md"}),
            ),
            text_reply("Sent to this chat."),
            text_reply("Telegram follow-up."),
            text_reply("Web response."),
        ],
    )
    dispatcher = TelegramDispatcher(api)
    update = message("Send a screenshot and report here", chat=-100, actor=42)
    update["message"].update(message_id=7, message_thread_id=8)
    try:
        await dispatcher.dispatch(update)
        await until(lambda: not dispatcher.tasks)
        messages, tools = llm.calls[0]
        prompt = "\n".join(m["content"] for m in messages if m["role"] == "system")
        assert "## Telegram conversation" in messages[0]["content"]
        assert all(
            "## Telegram conversation" not in m["content"]
            for m in messages[1:]
            if m["role"] == "system"
        )
        assert "Files panel" in prompt and "not" in prompt
        assert '<img src="/api/' not in prompt
        assert "## Generative UI" not in prompt
        schema = next(t for t in tools if t["function"]["name"] == "telegram_send_file")
        assert "chat_id" not in schema["function"]["parameters"]["properties"]
        assert not any(t["function"]["name"] == "render_ui" for t in tools)
        assert [m for m, _ in bot.uploads] == ["sendPhoto", "sendDocument"]
        for method, parts in bot.uploads:
            assert parts["chat_id"].get_payload(decode=True) == b"-100"
            assert parts["message_thread_id"].get_payload(decode=True) == b"8"
            assert (
                json.loads(parts["reply_parameters"].get_payload(decode=True))[
                    "message_id"
                ]
                == 7
            )
            file = parts["photo" if method == "sendPhoto" else "document"]
            assert file.get_payload(decode=True) == (
                image if method == "sendPhoto" else b"# Report"
            )
        sid = store.find_session("main", "tg_-100")
        assert (artifacts_dir(sid) / "shot.png").read_bytes() == image
        results = [m["content"] for m in llm.calls[2][0] if m["role"] == "tool"]
        assert len(results) == 4
        assert all(not r.startswith("Error:") for r in results)
        assert all(json.loads(r)["sent"] for r in results[-2:])

        follow_up = message("Follow up", chat=-100, actor=42)
        follow_up["message"].update(message_id=8, message_thread_id=8)
        await dispatcher.dispatch(follow_up)
        await until(lambda: not dispatcher.tasks)
        assert len(llm.calls) == 4
        assert all(m[0] == messages[0] and t == tools for m, t in llm.calls[:4])
        # The actual provider's cache routing key must also remain stable.
        from app.runtime.llm.codex_responses import CodexResponsesClient

        client = CodexResponsesClient(access_token="unused", model="gpt-5-codex")
        try:
            payloads = [client._payload(m, t) for m, t in llm.calls[:4]]
            assert len({p["prompt_cache_key"] for p in payloads}) == 1
            assert all(
                "## Telegram conversation" in p["instructions"] for p in payloads
            )
            assert all(p["tools"] == payloads[0]["tools"] for p in payloads)
        finally:
            await client.aclose()

        # A real Member web chat must not see Telegram conversation context.
        member = store.create_user({"username": "deliveryweb", "password": "password1", "role": "member"})
        profile = store.create_llm_profile({"name": "Web model", "model": "test-model", "api_key": "k"})
        store.access.assign("usr_admin", member["id"], "model", profile["id"])
        web_sid = store.get_or_create_session("main", member["id"])
        await run_channel_turn(web_sid, "Web question")
        web_messages, web_tools = llm.calls[-1]
        web_prompt = "\n".join(
            m["content"] for m in web_messages if m["role"] == "system"
        )
        assert "## Telegram conversation" not in web_prompt
        assert '<img src="/api/' in web_prompt
        assert not any(t["function"]["name"] == "telegram_send_file" for t in web_tools)
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_telegram_file_cannot_escape_session_or_claim_failed_upload(
    setup, monkeypatch
):
    bot, api = setup
    sid = store.get_or_create_session("main", "tg_42")
    other = store.get_or_create_session("main", "tg_-100")
    write_artifact_text(other, "secret.md", "another conversation")
    write_artifact_text(sid, "report.md", "current report")
    (artifacts_dir(sid) / "leak.md").symlink_to(artifacts_dir(other) / "secret.md")
    bot.reject_uploads = True
    llm = install(
        monkeypatch,
        [
            calls(
                ("telegram_send_file", {"filename": "../secret.md"}),
                ("telegram_send_file", {"filename": "leak.md"}),
                (
                    "telegram_send_file",
                    {"filename": "secret.md", "session_id": other, "chat_id": -100},
                ),
                ("telegram_send_file", {"filename": "report.md"}),
            ),
            text_reply("Delivery failed."),
        ],
    )
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message("Send my report"))
        await until(lambda: not dispatcher.tasks)
        results = [m["content"] for m in llm.calls[-1][0] if m["role"] == "tool"]
        assert len(results) == 4 and all(r.startswith("Error:") for r in results)
        assert "private-bot-token" not in str(results)
        assert len(bot.uploads) == 1
        assert (
            bot.uploads[0][1]["document"].get_payload(decode=True) == b"current report"
        )
        assert (artifacts_dir(sid) / "report.md").read_text() == "current report"
    finally:
        await dispatcher.close()
        await api.aclose()
