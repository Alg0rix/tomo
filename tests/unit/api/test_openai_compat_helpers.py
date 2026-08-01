"""Unit tests for OpenAI-compat helpers."""

from __future__ import annotations

from fastapi import Request
from starlette.datastructures import Headers

from app.api.openai_compat import last_user_message, parse_sse_block, resolve_session_id
from app.services import store


def test_last_user_message_plain() -> None:
    assert (
        last_user_message(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "again"},
            ]
        )
        == "again"
    )


def test_last_user_message_multimodal() -> None:
    assert (
        last_user_message(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "see this"},
                        {"type": "image_url", "image_url": {"url": "x"}},
                    ],
                }
            ]
        )
        == "see this"
    )


def test_parse_sse_block() -> None:
    name, data = parse_sse_block('event: delta\ndata: {"content":"hi"}\nid: 1')
    assert name == "delta"
    assert data == {"content": "hi"}


def test_resolve_session_id_uses_header(tmp_path) -> None:
    store.rebind(tmp_path / "resolve_hdr.db")
    sid = store.create_swarm_session(["main", "ops"], user_id="web")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": Headers({"x-tomo-session-id": sid}).raw,
        "query_string": b"",
        "client": ("test", 0),
        "server": ("test", 80),
        "scheme": "http",
    }
    request = Request(scope)
    got, err = resolve_session_id(request, agent_id="main", user_id="web")
    assert err is None
    assert got == sid


def test_resolve_session_id_missing_header_session(tmp_path) -> None:
    store.rebind(tmp_path / "resolve_missing.db")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": Headers({"x-tomo-session-id": "ses_nope"}).raw,
        "query_string": b"",
        "client": ("test", 0),
        "server": ("test", 80),
        "scheme": "http",
    }
    request = Request(scope)
    got, err = resolve_session_id(request, agent_id="main", user_id="web")
    assert got is None
    assert err and err["error"]["type"] == "not_found_error"
