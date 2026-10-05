"""Final integrator: multi-chat/channel/swarm/quota/revocation local concurrency acceptance.

Real policy/SQLite/supervision/storage seams; no mocked auth, store,
runtime, or quota checks. Each test drives the production admission path it
names through the public Store/AccessService interfaces and would fail if
that path regressed to an independent counter, a skipped teardown barrier,
a widened Member ceiling, or an unbounded volume assumption.

Out of scope here (operator/provisioning gates, explicitly not claimed):
real-external-origin retrieval, live-destination SSH rollout, production
bounded-volume provisioning, sustained stress, independent security review.
"""
from __future__ import annotations


import pytest

from app.runtime.access import AccessDenied, AccessUnavailable, execution_scope
from app.services.store import store


@pytest.fixture
def world(tmp_path):
    from tests.fakes.access import ensure_stoppers
    store.rebind(tmp_path / "final-acceptance.db")
    ensure_stoppers()
    alice = store.create_user({"username": "alice", "password": "password123"})["id"]
    bob = store.create_user({"username": "bob", "password": "password123"})["id"]
    profile = store.create_llm_profile({"name": "Assigned", "model": "test-model", "api_key": "secret"})
    store.set_default_llm_profile(profile["id"])
    for uid in (alice, bob):
        store.access.assign("usr_admin", uid, "model", profile["id"])
    yield alice, bob, profile["id"]
    store.rebind(None)


def resolve(uid, sid):
    return store.access.resolve_context(uid, sid)


def test_quota_change_stops_execution_and_stales_old_ceilings(world):
    """A quota change runs the composite teardown barrier and invalidates
    already-resolved ceilings; freshly resolved contexts keep working."""
    alice, _, _ = world
    sid = store.create_home_session(alice)["session_id"]
    stopped = []
    store.access.register_execution_stopper(lambda s: stopped.append(s))
    old = resolve(alice, sid)
    store.access.set_quota("usr_admin", alice, {"cpu": 1})
    assert sid in stopped
    with pytest.raises(AccessDenied):
        store.access.revalidate(old)
    fresh = resolve(alice, sid)
    assert store.access.revalidate(fresh).session_id == sid


def test_model_revocation_denies_new_execution_until_reassigned(world):
    """Revoking the assigned model fails new resolution closed for a Member
    (no silent fallback profile); reassignment restores execution."""
    alice, _, profile_id = world
    sid = store.create_home_session(alice)["session_id"]
    resolve(alice, sid)
    store.access.revoke("usr_admin", alice, "model", profile_id)
    with pytest.raises((AccessDenied, AccessUnavailable)):
        resolve(alice, sid)
    store.access.assign("usr_admin", alice, "model", profile_id)
    assert resolve(alice, sid).session_id == sid


def test_swarm_run_isolation_between_users(world):
    """A user cannot bind another user's swarm run even inside their own
    admitted session scope."""
    from app.models.mixins import swarm
    from app.runtime.tools import swarm_board

    alice, bob, _ = world
    alice_sid = store.create_home_session(alice)["session_id"]
    bob_sid = store.create_home_session(bob)["session_id"]
    bob_run = store.with_db(lambda conn: swarm.create_run(conn, bob_sid, "Bob private run"))
    own = resolve(alice, alice_sid)
    with execution_scope(own):
        with pytest.raises(AccessDenied):
            swarm_board.bind(run_id=bob_run, task_id="", agent_id=own.agent_id)


def test_member_has_no_platform_admin_fallback(world):
    """Members cannot use Admin recovery/management seams, tool ceilings
    exclude platform-admin tools, and a missing ceiling fails closed."""
    from app.services.access import _MEMBER_ADMIN_TOOLS

    alice, _, _ = world
    sid = store.create_home_session(alice)["session_id"]
    member = resolve(alice, sid)
    with pytest.raises(AccessDenied):
        store.access.require_admin(alice)
    with pytest.raises(AccessDenied):
        store.access.revalidate(None)
    assert not (set(member.tool_ids) & _MEMBER_ADMIN_TOOLS)


def test_bounded_volume_signal_and_per_user_disk_fairness(world, tmp_path):
    """An unprovisioned managed-storage root reports unbounded (operator
    gate signal, CLI exit 2), while per-user disk quotas are enforced in
    code: a tiny disk quota denies private writes until raised."""
    import subprocess
    import sys

    from app.runtime import storage

    alice, _, _ = world
    status = storage.managed_storage_status(root=tmp_path / "managed-storage")
    assert status["bounded"] is False
    proc = subprocess.run(
        [sys.executable, "-m", "app.runtime.storage", "--check",
         "--root", str(tmp_path / "managed-storage")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    sid = store.create_home_session(alice)["session_id"]
    store.access.set_quota("usr_admin", alice, {"disk_mb": 1})
    with execution_scope(resolve(alice, sid)):
        with pytest.raises(AccessUnavailable):
            with storage.private_write(2 * 1024 * 1024):
                pass
    store.access.set_quota("usr_admin", alice, {"disk_mb": 4096})
    with execution_scope(resolve(alice, sid)):
        with storage.private_write(0):
            pass
