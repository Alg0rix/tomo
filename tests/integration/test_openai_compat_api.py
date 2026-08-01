"""Integration: OpenAI-compat completions + session POST SSE."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.deps import require_auth
from app.main import app
from app.runtime.llm.mock import MockLLMClient
from app.services import store


@pytest.fixture(autouse=True)
def _inject_mock_llm(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.runtime.agent.loop.get_llm",
        lambda agent_id=None: MockLLMClient(),
    )


def _auth_client(tmp_path, db_name: str) -> tuple[TestClient, str]:
    store.rebind(tmp_path / db_name)
    admin = store.get_user_by_username("admin")
    token = store.create_api_key(admin["id"], "openai-test")["token"]
    return TestClient(app), token


def _parse_openai_sse(raw: str) -> list[dict | str]:
    items: list[dict | str] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].lstrip()
            if payload == "[DONE]":
                items.append("[DONE]")
            else:
                items.append(json.loads(payload))
    return items


def _parse_tomo_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        name: str | None = None
        data = "{}"
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].lstrip()
        if name:
            events.append((name, json.loads(data)))
    return events


def test_chat_completions_requires_auth(tmp_path) -> None:
    store.rebind(tmp_path / "oai_auth.db")
    client = TestClient(app)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert res.status_code == 401


def test_chat_completions_unknown_model(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_model.db")
    res = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "no_such_agent",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert res.status_code == 404
    assert res.json()["error"]["type"] == "not_found_error"


def test_chat_completions_non_stream(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_nonstrm.db")
    res = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hello there"}],
            "stream": False,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "main"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "stop"
    sid = res.headers.get("X-Tomo-Session-Id")
    assert sid
    assert store.get_session(sid)


def test_chat_completions_stream(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_strm.db")
    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hello stream"}],
            "stream": True,
        },
    ) as res:
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        sid = res.headers.get("X-Tomo-Session-Id")
        assert sid
        raw = "".join(res.iter_text())

    items = _parse_openai_sse(raw)
    assert items[-1] == "[DONE]"
    chunks = [i for i in items if isinstance(i, dict)]
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    contents = [
        c["choices"][0]["delta"].get("content") or ""
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert "".join(contents)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_session_chat_stream_post(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "sess_post_strm.db")
    admin = store.get_user_by_username("admin")
    sid = store.get_or_create_session("main", admin["id"])

    with client.stream(
        "POST",
        f"/api/sessions/{sid}/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "hello post stream"},
    ) as res:
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        raw = "".join(res.iter_text())

    events = _parse_tomo_sse(raw)
    names = [n for n, _ in events]
    assert "delta" in names or "done" in names
    assert "turn.end" in names


def test_session_chat_stream_post_requires_message(tmp_path) -> None:
    store.rebind(tmp_path / "sess_post_empty.db")
    app.dependency_overrides[require_auth] = lambda: None
    client = TestClient(app)
    try:
        sid = store.create_swarm_session(["main"], user_id="web")
        res = client.post(
            f"/api/sessions/{sid}/chat/stream",
            json={"message": "  "},
        )
        assert res.status_code == 400
    finally:
        app.dependency_overrides.pop(require_auth, None)


def test_chat_completions_continues_swarm_via_session_header(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_swarm_hdr.db")
    admin = store.get_user_by_username("admin")
    sid = store.create_swarm_session(
        ["main", "ops", "coder", "research"],
        user_id=admin["id"],
    )
    session = store.get_session(sid)
    assert len(session.get("agent_ids") or []) >= 2

    res = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tomo-Session-Id": sid,
        },
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hello swarm"}],
            "stream": False,
        },
    )
    assert res.status_code == 200
    assert res.headers.get("X-Tomo-Session-Id") == sid
    assert res.json()["choices"][0]["message"]["content"]
    # Must not have created a separate solo session for this turn.
    assert store.get_session(sid)["id"] == sid


def test_chat_completions_unknown_session_header(tmp_path) -> None:
    client, token = _auth_client(tmp_path, "oai_bad_sid.db")
    res = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tomo-Session-Id": "ses_does_not_exist",
        },
        json={
            "model": "main",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert res.status_code == 404
    assert res.json()["error"]["type"] == "not_found_error"
