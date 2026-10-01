"""/compact — session-history compaction markers."""

from __future__ import annotations

import pytest

from app.runtime.agent.context import history_to_messages
from app.services import store
from app.services.compact import compact_session, entries_after_last_compact
from tests.fakes.llm import ScriptedLLM, text_reply


def _seed_history(session_id: str, turns: int = 5) -> None:
    for i in range(turns):
        store.append_session_history(
            session_id, {"type": "user", "content": f"question {i}", "agent_id": "main"}
        )
        store.append_session_history(
            session_id, {"type": "final", "content": f"answer {i}", "agent_id": "main"}
        )


def _session(tmp_path) -> str:
    store.rebind(tmp_path / "compact.db")
    return store.get_or_create_session("main", "web")


def _fake_llm(monkeypatch, text: str = "Summary of earlier turns.") -> ScriptedLLM:
    llm = ScriptedLLM([text_reply(text)])
    monkeypatch.setattr("app.runtime.llm.get_llm", lambda *a, **kw: llm)
    return llm


async def test_compact_appends_marker_and_summarizes(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    _seed_history(session_id, turns=5)
    llm = _fake_llm(monkeypatch, "Talked about Q0-Q4, answered A0-A4.")

    result = await compact_session(session_id, agent_id="main")

    assert result["status"] == "ok"
    assert result["compacted"] == 10
    history = store.get_session_history(session_id)
    markers = [e for e in history if e.get("type") == "compact"]
    assert len(markers) == 1
    assert markers[0]["content"] == "Talked about Q0-Q4, answered A0-A4."
    assert llm.remaining == 0


async def test_compact_too_short_history(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    store.append_session_history(
        session_id, {"type": "user", "content": "hi", "agent_id": "main"}
    )
    llm = _fake_llm(monkeypatch)
    result = await compact_session(session_id, agent_id="main")
    assert result["status"] == "too_short"
    assert llm.remaining == 1  # no model call was spent
    assert not [e for e in store.get_session_history(session_id) if e["type"] == "compact"]


async def test_compact_no_history(tmp_path) -> None:
    session_id = _session(tmp_path)
    result = await compact_session(session_id, agent_id="main")
    assert result["status"] == "no_history"


def test_history_to_messages_respects_compact_marker() -> None:
    history = [
        {"type": "user", "content": "old q", "agent_id": "main"},
        {"type": "final", "content": "old a", "agent_id": "main"},
        {
            "type": "compact",
            "content": "We discussed old stuff.",
            "agent_id": "main",
        },
        {"type": "user", "content": "new q", "agent_id": "main"},
        {"type": "final", "content": "new a", "agent_id": "main"},
    ]
    msgs = history_to_messages(history, for_agent_id="main")
    texts = [m.get("content") for m in msgs if isinstance(m.get("content"), str)]
    assert not any("old q" in t for t in texts)
    assert not any("old a" in t for t in texts)
    assert any("compacted" in t and "We discussed old stuff." in t for t in texts)
    assert any(t == "new q" for t in texts)
    assert any(t == "new a" for t in texts)


def test_second_compact_marker_replaces_first() -> None:
    history = [
        {"type": "user", "content": "era1", "agent_id": "main"},
        {"type": "compact", "content": "first summary", "agent_id": "main"},
        {"type": "user", "content": "era2", "agent_id": "main"},
        {"type": "compact", "content": "latest summary", "agent_id": "main"},
        {"type": "user", "content": "era3", "agent_id": "main"},
    ]
    msgs = history_to_messages(history, for_agent_id="main")
    texts = [m.get("content") for m in msgs if isinstance(m.get("content"), str)]
    assert not any("era1" in t or "era2" in t or "first summary" in t for t in texts)
    assert any("latest summary" in t for t in texts)
    assert any(t == "era3" for t in texts)


def test_entries_after_last_compact() -> None:
    history = [
        {"type": "user", "content": "a"},
        {"type": "compact", "content": "s1"},
        {"type": "user", "content": "b"},
    ]
    live, start = entries_after_last_compact(history)
    assert start == 2  # first live entry index (marker is at 1)
    assert [e["content"] for e in live] == ["b"]
    live, start = entries_after_last_compact([{"type": "user", "content": "x"}])
    assert start == 0 and len(live) == 1


def test_image_attachments_before_compact_are_skipped(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "v.db")
    from app.services import vision as svc_vision

    calls = []
    monkeypatch.setattr(store, "get_attachment", lambda aid: calls.append(aid) or {
        "id": aid, "mime_type": "image/png", "file_path": "/x.png",
    })
    history = [
        {"type": "user", "content": "old", "attachment_ids": ["img_old"]},
        {"type": "compact", "content": "s"},
        {"type": "user", "content": "new", "attachment_ids": ["img_new"]},
    ]
    out = svc_vision.collect_image_attachments(history)
    assert [a["id"] for a in out] == ["img_new"]
    assert calls == ["img_new"]


async def test_auto_compact_triggers_over_threshold(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    # Entries large enough to pass the cheap rough-size gate before the full
    # context projection is consulted.
    for i in range(8):
        store.append_session_history(
            session_id,
            {"type": "user", "content": "question " + str(i) + " " + "x" * 200, "agent_id": "main"},
        )
        store.append_session_history(
            session_id,
            {"type": "final", "content": "answer " + str(i) + " " + "y" * 200, "agent_id": "main"},
        )
    _fake_llm(monkeypatch, "Auto summary of the thread.")
    monkeypatch.setattr(
        "app.runtime.agent.context_usage._resolve_context_limit",
        lambda *a, **kw: 100,
    )
    monkeypatch.setattr(
        "app.runtime.agent.context_usage.compute_context_usage",
        lambda *a, **kw: {"used": 95, "limit": 100, "percent": 95},
    )
    store.update_settings(
        {"auto_compact_enabled": True, "auto_compact_threshold": 0.9}
    )
    from app.services.compact import maybe_auto_compact

    result = await maybe_auto_compact(session_id, agent_id="main")
    assert result is not None and result["auto"] is True
    assert result["compacted"] == 16
    assert result["usage_before"]["percent"] == 95
    assert any(
        e.get("type") == "compact" for e in store.get_session_history(session_id)
    )


async def test_auto_compact_skips_under_threshold(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    _seed_history(session_id, turns=3)
    llm = _fake_llm(monkeypatch)
    from app.services.compact import maybe_auto_compact

    assert await maybe_auto_compact(session_id, agent_id="main") is None
    assert llm.remaining == 1


async def test_auto_compact_respects_kill_switch(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    _seed_history(session_id, turns=8)
    _fake_llm(monkeypatch)
    store.update_settings({"auto_compact_enabled": False})
    monkeypatch.setattr(
        "app.runtime.agent.context_usage._resolve_context_limit",
        lambda *a, **kw: 1,
    )
    from app.services.compact import maybe_auto_compact

    assert await maybe_auto_compact(session_id, agent_id="main") is None
