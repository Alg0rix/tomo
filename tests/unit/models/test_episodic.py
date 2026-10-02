"""Production episodic memory — structured experiences, dedupe, decay, feedback."""

from __future__ import annotations

from app.models.mixins import episodic as ep
from app.runtime.memory.episodes import build_from_review
from app.runtime.tools import record_episode, recall_episodes, user_ctx
from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "ep.db")


def test_structured_episode_events_and_scope(tmp_path) -> None:
    _rebind(tmp_path)
    a = store.insert_episode(
        {
            "user_id": "usr_alice",
            "title": "Service restore",
            "objective": "Restore service X after outage",
            "context_summary": "production, dependency Y",
            "trajectory_summary": "Checked metrics; found Y unavailable; restarted Y",
            "actions": ["restart Y"],
            "events": [
                {"type": "observation", "description": "Y unavailable"},
                {"type": "action", "description": "restart Y", "result": "success"},
            ],
            "outcome_status": "success",
            "outcome_summary": "Service recovered",
            "what_worked": ["Restart dependency Y first"],
            "what_failed": ["Blind restart of X"],
            "lessons": [{"statement": "Verify Y before modifying X", "confidence": 0.9}],
            "importance": 0.9,
            "confidence": 0.85,
            "utility": 0.8,
            "entities": ["service-x", "dependency-y"],
        }
    )
    assert a is not None
    assert a["content_hash"]
    assert a["outcome_status"] == "success"
    assert a["payload"]["reflection"]["what_worked"]
    events = store.list_episode_events(a["id"])
    assert len(events) >= 2

    store.insert_episode(
        {
            "user_id": "usr_bob",
            "objective": "Bob private outage",
            "outcome_summary": "dependency Y also mentioned for bob",
            "outcome_status": "failure",
        }
    )
    hits = store.search_episodes("dependency Y", user_id="usr_alice", limit=10)
    assert len(hits) >= 1
    assert all(h["user_id"] == "usr_alice" for h in hits)
    assert store.search_episodes("Bob private", user_id="usr_alice") == []


def test_dedupe_near_identical(tmp_path) -> None:
    _rebind(tmp_path)
    payload = {
        "user_id": "usr_alice",
        "objective": "Fix flaky CI",
        "trajectory_summary": "Reran tests with seed fixed",
        "outcome_status": "success",
        "outcome_summary": "CI green",
        "importance": 0.7,
        "confidence": 0.7,
        "utility": 0.7,
    }
    a = store.insert_episode(payload)
    b = store.insert_episode(payload)
    assert a is not None and b is not None
    assert a["id"] == b["id"]


def test_failure_and_feedback_and_decay(tmp_path) -> None:
    _rebind(tmp_path)
    ep0 = store.insert_episode(
        {
            "user_id": "usr_alice",
            "objective": "Apply config change",
            "trajectory_summary": "Applied X under condition Z",
            "outcome_status": "failure",
            "outcome_summary": "X caused outage Y",
            "what_failed": ["Applying X under Z"],
            "lessons": ["Never repeat X under condition Z"],
            "importance": 0.95,
            "confidence": 0.9,
            "utility": 0.9,
        }
    )
    assert ep0 is not None
    assert store.episode_feedback(ep0["id"], helpful=True, user_id="usr_alice")
    stats = store.decay_episodes(user_id="usr_alice")
    assert stats["updated"] >= 1
    hits = store.search_episodes("outage config X", user_id="usr_alice")
    assert hits
    # Negative-intent queries should still surface failures.
    assert any(h["outcome_status"] == "failure" for h in hits)


def test_supersede_and_link(tmp_path) -> None:
    _rebind(tmp_path)
    old = store.insert_episode(
        {
            "user_id": "u",
            "objective": "Deploy app",
            "outcome_summary": "used script A",
            "outcome_status": "partial",
            "force": True,
        }
    )
    new = store.insert_episode(
        {
            "user_id": "u",
            "objective": "Deploy app",
            "outcome_summary": "used script B which works",
            "outcome_status": "success",
            "force": True,
        }
    )
    assert old and new and old["id"] != new["id"]
    assert store.supersede_episode(old["id"], new["id"], user_id="u")
    with store._lock:
        o = ep.get_episode(store._conn, old["id"], user_id="u")
    assert o is not None
    assert o["state"] == "superseded"


def test_build_from_review_selective(tmp_path) -> None:
    _rebind(tmp_path)
    # Trivial turn → no episode
    assert (
        build_from_review(
            user_id="u",
            agent_id="main",
            session_id="s1",
            user_message="hi",
            final_content="hello",
            tool_calls=0,
        )
        is None
    )
    # Tool-heavy experience → episode
    ep0 = build_from_review(
        user_id="u",
        agent_id="main",
        session_id="s1",
        user_message="fix the deploy",
        final_content="Deploy fixed by setting FOO",
        tool_calls=4,
        diary="Remembered FOO env for deploy",
        actions=["bash: kubectl apply", "bash: kubectl rollout status"],
    )
    assert ep0 is not None
    assert ep0["user_id"] == "u"


def test_record_and_recall_tools(tmp_path) -> None:
    _rebind(tmp_path)
    tok = user_ctx.bind_user("usr_alice")
    try:
        msg = record_episode.run(
            {
                "title": "SSH flake",
                "objective": "Connect to bastion",
                "context_summary": "prod",
                "trajectory_summary": "connection reset; retry with keepalive",
                "outcome_status": "success",
                "outcome_summary": "connected",
                "importance": 0.7,
            }
        )
        assert msg.startswith("Recorded episode")
        out = recall_episodes.run({"query": "connection reset", "limit": 5})
        assert "SSH" in out or "bastion" in out or "keepalive" in out
    finally:
        user_ctx.reset_user(tok)
