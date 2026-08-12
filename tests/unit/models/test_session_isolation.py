"""Per-user chat session isolation (strict multi-user)."""

from __future__ import annotations

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "iso.db")


def test_list_sessions_filters_by_user(tmp_path) -> None:
    _rebind(tmp_path)
    a = store.create_swarm_session(["main"], user_id="usr_alice")
    b = store.create_swarm_session(["main"], user_id="usr_bob")
    store.create_swarm_session(["main"], user_id="web")

    alice = store.list_sessions(user_id="usr_alice")
    bob = store.list_sessions(user_id="usr_bob")
    all_rows = store.list_sessions()

    assert {s["id"] for s in alice} == {a}
    assert {s["id"] for s in bob} == {b}
    assert len(all_rows) >= 3


def test_get_owned_session_hides_other_users(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"], user_id="usr_alice")
    assert store.get_owned_session(sid, "usr_alice") is not None
    assert store.get_owned_session(sid, "usr_bob") is None
    assert store.get_owned_session("missing", "usr_alice") is None


def test_prune_drafts_only_own_user(tmp_path) -> None:
    _rebind(tmp_path)
    alice_draft = store.create_swarm_session(["main"], user_id="usr_alice")
    bob_draft = store.create_swarm_session(["main"], user_id="usr_bob")
    deleted = store.prune_empty_draft_sessions(user_id="usr_alice")
    assert alice_draft in deleted
    assert bob_draft not in deleted
    assert store.get_session(bob_draft) is not None
    assert store.get_session(alice_draft) is None


def test_dashboard_recent_sessions_scoped(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_swarm_session(["main"], user_id="usr_alice")
    store.create_swarm_session(["main"], user_id="usr_bob")
    data = store.dashboard_data(user_id="usr_alice")
    ids = {s["id"] for s in data["recent_sessions"]}
    assert all(
        store.get_session(sid)["user_id"] == "usr_alice" for sid in ids
    )
    assert data["stats"]["session_count"] == 1
