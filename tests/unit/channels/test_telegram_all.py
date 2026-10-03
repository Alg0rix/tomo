"""Consolidated tests (merged from: test_telegram.py, test_telegram_format.py).
- test_telegram.py: Telegram channel — mocked Bot API (no network) + turn pipeline.
- test_telegram_format.py: Telegram rendering preserves text and produces independently valid chunks.
"""

from __future__ import annotations

import json
import httpx2
import pytest
from app.channels.telegram import (
    TelegramAPI,
    extract_text_message,
    handle_inbound_text,
    poll_once,
    process_update,
    run_channel_turn,
)
from app.services import store
from tests.fakes.llm import ScriptedLLM, text_reply
import html
from html.parser import HTMLParser
from app.channels.telegram_format import (
    plain_text,
    render_markdown,
    split_html,
    utf16_len,
)


# --- from test_telegram.py ---
@pytest.fixture(autouse=True)
def _inject_scripted_llm(monkeypatch) -> None:
    client = ScriptedLLM([text_reply("Telegram reply.")] * 20)
    monkeypatch.setattr(
        "app.runtime.agent.loop.get_llm",
        lambda agent_id=None: client,
    )


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "tg-channel.db")
    store.update_settings({"telegram_allowed_chat_ids": ["4242", "9", "1"]})




def test_extract_text_message() -> None:
    assert extract_text_message(
        {"update_id": 1, "message": {"chat": {"id": 7}, "text": " hello "}}
    ) == (7, "hello")
    assert extract_text_message({"update_id": 2, "message": {"chat": {"id": 1}}}) is None
    assert extract_text_message({"update_id": 3}) is None


async def test_api_get_updates_and_send_message_mocked() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        assert "SECRETTOKEN" in str(request.url)
        if request.url.path.endswith("/getUpdates"):
            return httpx2.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 10,
                            "message": {
                                "message_id": 1,
                                "chat": {"id": 55, "type": "private"},
                                "text": "ping",
                            },
                        }
                    ],
                },
            )
        if request.url.path.endswith("/sendMessage"):
            body = json.loads(request.content.decode())
            assert body["chat_id"] == 55
            assert "pong" in body["text"] or body["text"]
            return httpx2.Response(
                200,
                json={"ok": True, "result": {"message_id": 2, "chat": {"id": 55}}},
            )
        return httpx2.Response(404, json={"ok": False})

    api = TelegramAPI("123:SECRETTOKEN", transport=httpx2.MockTransport(handler))
    updates = await api.get_updates(offset=0, timeout=0)
    assert len(updates) == 1
    assert updates[0]["update_id"] == 10
    await api.send_message(55, "hello from tomo")
    assert any(p.endswith("/getUpdates") for p in calls)
    assert any(p.endswith("/sendMessage") for p in calls)
    await api.aclose()


async def test_handle_inbound_maps_chat_to_session_and_replies(tmp_path) -> None:
    _rebind(tmp_path)
    sent: list[tuple[int | str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/sendMessage"):
            body = json.loads(request.content.decode())
            sent.append((body["chat_id"], body["text"]))
            return httpx2.Response(200, json={"ok": True, "result": {}})
        return httpx2.Response(404)

    api = TelegramAPI("tok:mock", transport=httpx2.MockTransport(handler))
    result = await handle_inbound_text(4242, "hello there", api=api, send_reply=True)
    await api.aclose()

    assert result["agent_id"] == "main"
    assert result["session_id"]
    assert result["reply"]
    assert sent and sent[0][0] == 4242
    assert sent[0][1] == result["reply"]

    session = store.get_session(result["session_id"])
    assert session is not None
    assert session["user_id"] == "tg_4242"
    hist = store.get_session_history(result["session_id"])
    assert any(e.get("type") == "user" and e.get("content") == "hello there" for e in hist)
    assert any(e.get("type") == "final" for e in hist)

    # Same chat reuses the same single-agent session.
    again = await handle_inbound_text(4242, "second", api=None, send_reply=False)
    assert again["session_id"] == result["session_id"]


async def test_run_channel_turn_persists_history(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.get_or_create_session("main", "tg_1")
    reply = await run_channel_turn(sid, "hi")
    assert reply
    hist = store.get_session_history(sid)
    assert hist[0]["type"] == "user"
    assert any(e["type"] == "final" for e in hist)


async def test_process_update_ignores_non_text(tmp_path) -> None:
    _rebind(tmp_path)
    assert await process_update({"update_id": 1, "message": {"chat": {"id": 1}}}) is None


async def test_poll_once_processes_batch(tmp_path) -> None:
    _rebind(tmp_path)
    sent: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx2.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 100,
                            "message": {
                                "chat": {"id": 9},
                                "text": "calculate 2 + 2",
                            },
                        }
                    ],
                },
            )
        if request.url.path.endswith("/sendMessage"):
            body = json.loads(request.content.decode())
            sent.append(body["text"])
            return httpx2.Response(200, json={"ok": True, "result": {}})
        return httpx2.Response(404)

    api = TelegramAPI("tok:poll", transport=httpx2.MockTransport(handler))
    next_off = await poll_once(api, offset=0, timeout=0)
    await api.aclose()
    assert next_off == 101
    assert sent  # reply delivered
    sid = store.get_or_create_session("main", "tg_9")
    assert any(e.get("type") == "user" for e in store.get_session_history(sid))


async def test_denied_chat_never_creates_session_or_runs_model(tmp_path, monkeypatch):
    _rebind(tmp_path)
    async def forbidden(*args):
        pytest.fail("Unapproved chat reached the model")
    monkeypatch.setattr("app.channels.telegram.run_channel_turn", forbidden)
    before = store.list_sessions()
    result = await handle_inbound_text(999, "secret request", send_reply=False)
    assert result["denied"] is True
    assert store.list_sessions() == before
    store.update_settings({"telegram_allowed_chat_ids": []})
    assert (await handle_inbound_text(4242, "hi", send_reply=False))["denied"]


async def test_id_and_help_do_not_call_model(tmp_path, monkeypatch):
    _rebind(tmp_path)
    async def forbidden(*args):
        pytest.fail("Command reached the model")
    monkeypatch.setattr("app.channels.telegram.run_channel_turn", forbidden)
    before = store.list_sessions()
    identity = await handle_inbound_text(-10099, "/id", send_reply=False)
    assert "-10099" in identity["reply"]
    assert store.list_sessions() == before
    help_result = await handle_inbound_text(4242, "/help", send_reply=False)
    assert "/new" in help_result["reply"]
    fresh = await handle_inbound_text(4242, "/new", send_reply=False)
    assert fresh["session_id"] != help_result["session_id"]
    assert store.find_session("main", "tg_4242") == fresh["session_id"]




# --- from test_telegram_format.py ---
class TagChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "i", "s", "code", "pre", "a", "blockquote"}
        self.stack.append(tag)
        self.tags.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag


def assert_valid(text):
    checker = TagChecker()
    checker.feed(text)
    assert not checker.stack
    return checker


def test_rich_markdown_and_untrusted_html():
    source = '# Title\n\n**bold _nested_** and `a < b` [link](https://example.com/?a=1&b=2)\n\n- first\n- second\n\n> quote\n\n```python\nprint("<tag>")\n```\n\n<script>unsafe</script>'
    rendered = render_markdown(source)
    checker = assert_valid(rendered)
    assert {"b", "i", "code", "pre", "a", "blockquote"} <= set(checker.tags)
    assert "<script>" not in rendered
    assert "<script>unsafe</script>" in plain_text(rendered)
    assert 'print("<tag>")' in plain_text(rendered)
    assert "• first" in plain_text(rendered)


def test_tables_are_readable_on_mobile():
    rendered = render_markdown(
        "| Service | State |\n| --- | --- |\n| api | **up** |\n| db | down |"
    )
    assert "Service: api" in plain_text(rendered)
    assert "State: up" in plain_text(rendered)
    assert "Service: db" in plain_text(rendered)
    assert_valid(rendered)


def test_long_unicode_code_and_links_split_without_loss():
    text = '😀 & < > " x\n' * 1400
    source = (
        "```python\n"
        + text
        + "```\n\n["
        + ("nested **bold** " * 1000)
        + "](https://example.com)"
    )
    rendered = render_markdown(source)
    chunks = split_html(rendered)
    assert len(chunks) > 2
    for chunk in chunks:
        assert utf16_len(chunk) <= 3900
        assert_valid(chunk)
    assert "".join(plain_text(c) for c in chunks) == plain_text(rendered)
    assert text in plain_text(rendered)


def test_unclosed_code_is_rendered_and_unsafe_link_stays_text():
    for source in [
        "```\n<oops>& 😀",
        "[label](javascript:alert(1))",
        "[**nested** label](/relative)",
        "**half",
    ]:
        rendered = render_markdown(source)
        assert_valid(rendered)
        assert 'href="javascript:' not in rendered
    assert html.unescape(plain_text(render_markdown("```\n<oops>& 😀"))) == "<oops>& 😀"


def test_telegram_forbidden_entity_nesting_is_flattened_without_losing_text():
    source = "**bold `code` end** [link `code`](https://example.com)\n\n> outside\n> > inside\n> `quoted code`\n> ```\n> fenced code\n> ```"
    rendered = render_markdown(source)
    assert_valid(rendered)
    assert "<b>bold </b><code>code</code><b> end</b>" in rendered
    assert rendered.count("<blockquote>") == 1
    quote = rendered.split("<blockquote>")[1].split("</blockquote>")[0]
    assert "<code>" not in quote and "<pre>" not in quote
    assert "fenced code" in quote and "quoted code" in quote


