"""Full multi-user isolation: knowledge, messages, companion, USER.md."""

from __future__ import annotations

from pathlib import Path

from app.core import config, home
from app.models.mixins import learning_events as le
from app.models.mixins import messages as msg_mod
from app.runtime.memory import curated
from app.runtime.tools import session_search, user_ctx
from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "mu.db")


def test_knowledge_scoped_per_user(tmp_path: Path) -> None:
    _rebind(tmp_path)
    a = store.create_knowledge_entry(
        {"title": "Alice secret", "body": "key-a", "user_id": "usr_alice"}
    )
    store.create_knowledge_entry(
        {"title": "Bob secret", "body": "key-b", "user_id": "usr_bob"}
    )
    alice_list = store.list_knowledge_entries(user_id="usr_alice")
    bob_list = store.list_knowledge_entries(user_id="usr_bob")
    assert {e["id"] for e in alice_list} == {a["id"]}
    assert all(e["user_id"] == "usr_alice" for e in alice_list)
    assert all(e["user_id"] == "usr_bob" for e in bob_list)
    assert store.get_knowledge_entry(a["id"], user_id="usr_bob") is None
    assert store.search_knowledge("secret", user_id="usr_alice")
    assert not any(
        h["id"] == a["id"]
        for h in store.search_knowledge("secret", user_id="usr_bob")
    )


def test_session_search_tool_scoped(tmp_path: Path) -> None:
    _rebind(tmp_path)
    alice_sid = store.create_swarm_session(["main"], user_id="usr_alice")
    bob_sid = store.create_swarm_session(["main"], user_id="usr_bob")
    store.append_session_history(
        alice_sid, {"type": "user", "content": "alice uniquephrase", "ts": 1.0}
    )
    store.append_session_history(
        bob_sid, {"type": "user", "content": "bob uniquephrase", "ts": 1.0}
    )
    tok = user_ctx.bind_user("usr_alice")
    try:
        out = session_search.run({"query": "uniquephrase", "limit": 10})
    finally:
        user_ctx.reset_user(tok)
    assert "alice uniquephrase" in out
    assert "bob uniquephrase" not in out


def test_message_search_like_user_filter(tmp_path: Path) -> None:
    _rebind(tmp_path)
    a = store.create_swarm_session(["main"], user_id="usr_a")
    b = store.create_swarm_session(["main"], user_id="usr_b")
    store.append_session_history(
        a, {"type": "user", "content": "sharedword from a", "ts": 1.0}
    )
    store.append_session_history(
        b, {"type": "user", "content": "sharedword from b", "ts": 1.0}
    )
    with store._lock:
        hits = msg_mod.search_messages(
            store._conn, "sharedword", limit=10, user_id="usr_a"
        )
    assert len(hits) == 1
    assert hits[0]["session_id"] == a


def test_companion_scoped(tmp_path: Path) -> None:
    _rebind(tmp_path)
    a = store.create_swarm_session(["main"], user_id="usr_alice")
    b = store.create_swarm_session(["main"], user_id="usr_bob")
    store.append_session_history(
        a, {"type": "user", "content": "hi alice", "ts": 10.0}
    )
    store.append_session_history(
        b, {"type": "user", "content": "hi bob", "ts": 20.0}
    )
    with store._lock:
        le.insert_learning_event(
            store._conn,
            agent_id="main",
            session_id=a,
            user_id="usr_alice",
            reason="test",
            saved=True,
            diary="alice diary",
        )
        le.insert_learning_event(
            store._conn,
            agent_id="main",
            session_id=b,
            user_id="usr_bob",
            reason="test",
            saved=True,
            diary="bob diary",
        )
    snap_a = store.companion_snapshot(user_id="usr_alice")
    snap_b = store.companion_snapshot(user_id="usr_bob")
    assert snap_a["bond_parts"]["chats"] == 1
    assert snap_b["bond_parts"]["chats"] == 1
    diaries_a = [e.get("diary") for e in snap_a["recent_events"]]
    diaries_b = [e.get("diary") for e in snap_b["recent_events"]]
    assert "alice diary" in diaries_a
    assert "bob diary" not in diaries_a
    assert "bob diary" in diaries_b


def test_user_md_per_account(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    home.ensure_tomo_home(tmp_path)
    curated.reset_freeze()

    tok_a = user_ctx.bind_user("usr_alice")
    try:
        r = curated.add_entry("user", "Alice likes tea", agent_id="main")
        assert r["ok"] is True
        path_a = home.user_memory_path("usr_alice", tmp_path)
        assert path_a.is_file()
        assert "tea" in path_a.read_text(encoding="utf-8")
    finally:
        user_ctx.reset_user(tok_a)

    tok_b = user_ctx.bind_user("usr_bob")
    try:
        listed = curated.list_entries("user", agent_id="main")
        assert listed["count"] == 0
        curated.add_entry("user", "Bob likes coffee", agent_id="main")
        path_b = home.user_memory_path("usr_bob", tmp_path)
        assert "coffee" in path_b.read_text(encoding="utf-8")
        assert "tea" not in path_b.read_text(encoding="utf-8")
    finally:
        user_ctx.reset_user(tok_b)

    alice_again = curated.read_user_entries(user_id="usr_alice")
    assert any("tea" in e for e in alice_again)


def test_agent_memory_md_per_account(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    home.ensure_tomo_home(tmp_path)
    curated.reset_freeze()

    tok_a = user_ctx.bind_user("usr_alice")
    try:
        r = curated.add_entry(
            "memory", "Alice's staging host is a.example", agent_id="ops"
        )
        assert r["ok"] is True
        path_a = home.agent_memory_path("ops", tmp_path, user_id="usr_alice")
        assert path_a.is_file()
        assert "a.example" in path_a.read_text(encoding="utf-8")
    finally:
        user_ctx.reset_user(tok_a)

    tok_b = user_ctx.bind_user("usr_bob")
    try:
        listed = curated.list_entries("memory", agent_id="ops")
        assert listed["count"] == 0
        curated.add_entry(
            "memory", "Bob's staging host is b.example", agent_id="ops"
        )
        path_b = home.agent_memory_path("ops", tmp_path, user_id="usr_bob")
        text_b = path_b.read_text(encoding="utf-8")
        assert "b.example" in text_b
        assert "a.example" not in text_b
    finally:
        user_ctx.reset_user(tok_b)

    alice_mem = curated.read_agent_entries("ops", user_id="usr_alice")
    assert any("a.example" in e for e in alice_mem)
