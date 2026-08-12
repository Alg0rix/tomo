"""Real episodic memories — concrete past experiences per user."""

from __future__ import annotations

from app.runtime.tools import record_episode, recall_episodes, user_ctx
from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "ep.db")


def test_insert_and_search_scoped_by_user(tmp_path) -> None:
    _rebind(tmp_path)
    a = store.insert_episode(
        {
            "user_id": "usr_alice",
            "title": "Deploy failed",
            "tried": "deployment X",
            "context": "project Y",
            "error": "error Z missing secret",
            "fix": "set FOO env",
            "outcome": "succeeded",
        }
    )
    assert a is not None
    store.insert_episode(
        {
            "user_id": "usr_bob",
            "title": "Bob only",
            "tried": "something else",
            "error": "error Z sharedword",
            "outcome": "failed",
        }
    )
    hits = store.search_episodes("error Z", user_id="usr_alice", limit=10)
    assert len(hits) == 1
    assert hits[0]["id"] == a["id"]
    assert "deployment X" in hits[0]["summary"]
    assert store.search_episodes("Bob only", user_id="usr_alice") == []


def test_record_and_recall_tools(tmp_path) -> None:
    _rebind(tmp_path)
    tok = user_ctx.bind_user("usr_alice")
    try:
        msg = record_episode.run(
            {
                "title": "SSH flake",
                "tried": "ssh into bastion",
                "context": "prod",
                "error": "connection reset",
                "fix": "retry with keepalive",
                "outcome": "succeeded",
            }
        )
        assert msg.startswith("Recorded episode")
        out = recall_episodes.run({"query": "connection reset", "limit": 5})
        assert "SSH flake" in out
    finally:
        user_ctx.reset_user(tok)

    tok_b = user_ctx.bind_user("usr_bob")
    try:
        out = recall_episodes.run({"query": "connection reset", "limit": 5})
        assert "No episodes matched" in out or "SSH flake" not in out
    finally:
        user_ctx.reset_user(tok_b)
