"""Stage 4: authorized remote workplaces + cross-machine transfer.

Real seams only: the live hub, real admission/envelope negotiation, an
in-process destination that enforces the contract against temp dirs, real
SQLite policy, real subprocess/file execution. No mocked authorization.
The only stand-in is the destination itself (no real external hosts); it
enforces owner/session/generation/scopes/RO/deadlines exactly like the Go
connector, whose own enforcement is covered by Go tests.
"""
import time
import uuid

import pytest

from app.runtime.access import AccessDenied, AccessUnavailable, execution_scope
from app.services import store
from app.workplaces import remote_contract
from app.workplaces.hub import hub
from tests.fakes.remote import FakeDestination

pytestmark = pytest.mark.skipif(
    __import__("os").name != "posix", reason="Remote destinations require POSIX")


def _unique(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def world(tmp_path):
    from tests.fakes.access import ensure_stoppers
    store.rebind(tmp_path / "remote.db")
    remote_contract.reset()
    ensure_stoppers()
    hub.reset()
    tag = uuid.uuid4().hex[:8]
    admin = store.create_user({"username": f"r4_admin_{tag}", "password": "password1", "role": "admin"})
    alice = store.create_user({"username": f"r4_alice_{tag}", "password": "password1", "role": "member"})
    bob = store.create_user({"username": f"r4_bob_{tag}", "password": "password1", "role": "member"})
    profile = store.create_llm_profile({"name": f"Assigned {tag}", "model": "local-test"})
    for uid in (alice["id"], bob["id"]):
        store.access.assign(admin["id"], uid, "model", profile["id"])
    destinations = {}
    try:
        for wid in ("wp_a", "wp_b"):
            wp = store.create_workplace({"id": f"{wid}_{tag}", "name": wid.upper(), "kind": "tunnel"})
            store.pair_connector(wp["pairing_code"], hostname="dest", version="0.4.0")
            dest = FakeDestination(
                wp["id"], tmp_path / f"dest-{wid}",
                caps="idempotent-replay,exec-stream,exec-context-v1,remote-sandbox-v1",
                sandbox_image="sha256:test",
            )
            dest.register()
            destinations[wid] = (wp["id"], dest)
        yield {"admin": admin, "alice": alice, "bob": bob, "dest": destinations, "tag": tag}
    finally:
        for _, dest in destinations.values():
            dest.unregister()
        hub.reset()
        remote_contract.reset()


def grant_all(world, user_id, wid, permission="read_write", unrestricted=False):
    admin = world["admin"]
    if permission is None:
        store.access.revoke(admin["id"], user_id, "workplace", wid)
    else:
        store.access.assign(admin["id"], user_id, "workplace", wid, permission=permission)
    if unrestricted:
        store.access.assign(admin["id"], user_id, "unrestricted", wid)


def chat(world, user_id, active, additional=(), mode="restricted", ack=False):
    sid = store.create_swarm_session(["main"], user_id=user_id)
    store.access.set_chat_access(user_id, sid, active, additional_workplace_ids=list(additional),
                                 execution_mode=mode, unrestricted_acknowledged=ack)
    return sid


def ctx_for(user_id, sid, agent_id="main"):
    return store.access.resolve_context(user_id, sid, agent_id)


def test_offline_tunnel_denies_without_local_fallback(world):
    alice = world["alice"]
    wid, dest = world["dest"]["wp_a"]
    dest.unregister()
    grant_all(world, alice["id"], wid)
    sid = chat(world, alice["id"], wid)
    # No execution identity can even form for an offline destination:
    # fail-closed at the access layer, never a local fallback.
    with pytest.raises(AccessUnavailable, match="(?i)offline"):
        ctx_for(alice["id"], sid)
    # Offline at the hub too: pairing alone never makes a destination live.
    assert not hub.is_online(wid)


def test_unsupported_connector_version_denies(tmp_path, world):
    alice = world["alice"]
    wp = store.create_workplace({"id": _unique("wp_old"), "name": "Old", "kind": "tunnel"})
    store.pair_connector(wp["pairing_code"], hostname="old", version="0.3.3")
    old = FakeDestination(wp["id"], tmp_path / "old", version="0.3.3",
                          caps="idempotent-replay,exec-stream")
    old.register()
    try:
        assert hub.is_online(wp["id"])
        grant_all(world, alice["id"], wp["id"], unrestricted=True)
        sid = chat(world, alice["id"], wp["id"], mode="unrestricted", ack=True)
        from app.runtime.tools import workplace_remote as wr
        with execution_scope(ctx_for(alice["id"], sid)):
            with pytest.raises(AccessUnavailable, match="(?i)update the connector"):
                wr.resolve_agent_workplace()
            # No transport without enforcement: the call refuses too.
            with pytest.raises(AccessUnavailable, match="(?i)update the connector"):
                wr._call_tunnel(store.get_workplace(wp["id"]), "exec_bash",
                                {"script": "echo hi"}, 5.0)
    finally:
        old.unregister()


def test_restricted_needs_sandbox_unrestricted_runs_with_grant(world):
    alice = world["alice"]
    wid, dest = world["dest"]["wp_a"]
    # Plain caps: no sandbox attestation (reconnect negotiates like hello).
    dest.unregister()
    dest.caps = "idempotent-replay,exec-stream,exec-context-v1"
    dest.register()
    grant_all(world, alice["id"], wid)
    from app.runtime.tools import workplace_remote as wr
    sid = chat(world, alice["id"], wid)
    with execution_scope(ctx_for(alice["id"], sid)):
        with pytest.raises(AccessUnavailable, match="(?i)restricted boundary"):
            wr.resolve_agent_workplace()
    # Unrestricted with matching grant + explicit acknowledgement runs.
    grant_all(world, alice["id"], wid, unrestricted=True)
    sid2 = chat(world, alice["id"], wid, mode="unrestricted", ack=True)
    with execution_scope(ctx_for(alice["id"], sid2)):
        out = wr.try_remote("exec_bash", {"script": "echo unres-ok"})
        assert "unres-ok" in (out or "")
    # Same grant without acknowledgement still denies.
    sid3 = store.create_swarm_session(["main"], user_id=alice["id"])
    with pytest.raises(AccessDenied):
        store.access.set_chat_access(alice["id"], sid3, wid, execution_mode="unrestricted",
                                     unrestricted_acknowledged=False)
    dest.unregister()
    dest.caps = "idempotent-replay,exec-stream,exec-context-v1,remote-sandbox-v1"
    dest.register()


def test_restricted_remote_io_ro_escape_and_owner(world):
    alice, bob = world["alice"], world["bob"]
    wid, dest = world["dest"]["wp_a"]
    # Operator-visible capability flags follow the live hello negotiation.
    public = store.get_workplace(wid)
    assert public["online"] is True
    assert public["remote_exec_ok"] is True
    assert public["remote_sandbox_ok"] is True
    assert public["connector_version"] == "0.4.0"
    grant_all(world, alice["id"], wid)
    from app.runtime.tools import workplace_remote as wr
    from app.runtime.tools import read_file, write_file
    sid = chat(world, alice["id"], wid)
    with execution_scope(ctx_for(alice["id"], sid)):
        assert "hello-remote" in wr.try_remote("exec_bash", {"script": "echo hello-remote"})
        assert "Wrote" in write_file.run({"path": "note.txt", "content": "hello"})
        assert "1|hello" in read_file.run({"path": "note.txt"})
        # Absolute host escape denied at the destination.
        assert read_file.run({"path": "/etc/hostname"}).startswith("Error:")
        # Sibling traversal outside admitted scopes denied.
        assert read_file.run({"path": "../outside.txt"}).startswith("Error:")
    # Another member without a grant cannot even resolve the destination.
    sid_bob = store.create_swarm_session(["main"], user_id=bob["id"])
    with pytest.raises(AccessDenied):
        store.access.set_chat_access(bob["id"], sid_bob, wid)
    # A forged envelope (wrong owner, stale generation) is refused
    # destination-side even if it reached the transport.
    forged = remote_contract.build_envelope(ctx_for(alice["id"], sid), wid)
    forged["owner_user_id"] = bob["id"]
    assert not dest.handle("exec_bash", {"exec_context": forged, "script": "id"}).get("ok")
    stale = remote_contract.build_envelope(ctx_for(alice["id"], sid), wid)
    stale["access_generation"] = -1
    assert not dest.handle("exec_bash", {"exec_context": stale, "script": "id"}).get("ok")


def test_read_only_scope_rejects_writes(world):
    alice = world["alice"]
    admin = world["admin"]
    wid, _ = world["dest"]["wp_a"]
    from app.runtime.tools import read_file, write_file
    # Seed shared content through a writable scope first.
    grant_all(world, admin["id"], wid, unrestricted=True)
    asid = chat(world, admin["id"], wid, mode="unrestricted", ack=True)
    with execution_scope(ctx_for(admin["id"], asid)):
        assert "Wrote" in write_file.run({"path": "shared.txt", "content": "shared"})
    # Read-only member reads but cannot write through any tool path.
    grant_all(world, alice["id"], wid, permission="read")
    sid = chat(world, alice["id"], wid)
    with execution_scope(ctx_for(alice["id"], sid)):
        assert "shared" in read_file.run({"path": "shared.txt"})
        assert write_file.run({"path": "shared.txt", "content": "overwrite"}).startswith("Error:")
        assert write_file.run({"path": "new.txt", "content": "new"}).startswith("Error:")


def test_generation_revocation_denies_new_work(world):
    alice = world["alice"]
    wid, dest = world["dest"]["wp_a"]
    grant_all(world, alice["id"], wid, unrestricted=True)
    from app.runtime.tools import workplace_remote as wr
    sid = chat(world, alice["id"], wid, mode="unrestricted", ack=True)
    with execution_scope(ctx_for(alice["id"], sid)):
        assert "before" in wr.try_remote("exec_bash", {"script": "echo before"})
    # Revoke: generation bumps, teardown kills, new work fails closed.
    grant_all(world, alice["id"], wid, permission=None)
    with pytest.raises(AccessDenied):
        ctx_for(alice["id"], sid)
    # The destination also rejects the pre-revocation envelope directly.
    assert not dest.handle("exec_bash", {"exec_context": {
        "v": 1, "owner_user_id": alice["id"], "session_id": sid, "agent_id": "main",
        "execution_mode": "unrestricted", "destination_id": wid,
        "active_workplace_id": wid, "access_generation": 0,
        "resources": [{"workplace_id": wid, "permission": "read_write", "destination_id": wid}],
        "quota": {"duration_seconds": 60},
    }, "script": "echo after"}).get("ok")


def test_confirmed_teardown_on_session_stop(world):
    alice = world["alice"]
    wid, dest = world["dest"]["wp_a"]
    grant_all(world, alice["id"], wid, unrestricted=True)
    from app.services.background_jobs import manager
    sid = chat(world, alice["id"], wid, mode="unrestricted", ack=True)
    with execution_scope(ctx_for(alice["id"], sid)):
        job = manager.start("sleep 60")
        assert job["backend"] == "tunnel"
        handle = job["backend_handle"]
        assert handle
    # Revoking access stops the session: the destination must confirm the
    # kill, otherwise the access barrier stays pending. Success here means
    # the teardown acknowledgement arrived.
    grant_all(world, alice["id"], wid, permission=None)
    assert store.get_session(sid)["access_pending"] == 0
    assert dest._jobs[handle].proc.poll() is not None


def test_remote_background_lifecycle_and_ssh_denied(world):
    alice = world["alice"]
    wid, _ = world["dest"]["wp_a"]
    grant_all(world, alice["id"], wid, unrestricted=True)
    from app.services.background_jobs import manager
    sid = chat(world, alice["id"], wid, mode="unrestricted", ack=True)
    with execution_scope(ctx_for(alice["id"], sid)):
        job = manager.start("echo bg-ok")
        assert job["backend"] == "tunnel"
        assert job["backend_handle"]
        deadline = time.time() + 10
        while time.time() < deadline:
            current = manager.get_job(sid, job["id"], user_id=alice["id"])
            if current["status"] in ("succeeded", "failed", "stopped", "unknown"):
                break
            time.sleep(0.1)
        stopped = manager.stop_job(sid, job["id"], user_id=alice["id"])
        assert stopped["status"] in ("succeeded", "failed", "stopped", "stopping", "unknown")
    # SSH background has no destination enforcement: denied even when the
    # chat properly targets the SSH destination with grant + acknowledgement.
    swp = store.create_workplace({"id": _unique("wp_ssh"), "name": "SSH", "kind": "ssh",
                                  "ssh_host": "h", "ssh_user": "u", "ssh_password": "p"})
    grant_all(world, alice["id"], swp["id"], unrestricted=True)
    from app.services import background_job_backends as backends
    # Simulate a probed-online SSH host (the probe itself needs a real host;
    # background denial must hold even when the host is reachable).
    store.with_db(lambda c: (c.execute("UPDATE workplaces SET status='connected' WHERE id=?", (swp["id"],)), c.commit()))
    ssh_sid = store.create_swarm_session(["main"], user_id=alice["id"])
    store.access.set_chat_access(alice["id"], ssh_sid, swp["id"],
                                 execution_mode="unrestricted", unrestricted_acknowledged=True)
    with execution_scope(ctx_for(alice["id"], ssh_sid)):
        with pytest.raises(AccessUnavailable):
            backends.start_remote("ssh", swp["id"], "echo hi", "/")


def test_cross_machine_transfer_success_and_denials(tmp_path, world, monkeypatch):
    from app.core import config
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("TOMO_WORK", str(work))
    monkeypatch.setattr(config, "TOMO_WORK", work)
    alice, bob = world["alice"], world["bob"]
    wid_a, _ = world["dest"]["wp_a"]
    wid_b, _ = world["dest"]["wp_b"]
    grant_all(world, alice["id"], wid_a, unrestricted=True)
    grant_all(world, alice["id"], wid_b)
    from app.runtime.tools import workplace_remote as wr
    # Additional resources may live on another machine: transfer endpoints.
    sid = chat(world, alice["id"], wid_a, additional=(wid_b,), mode="unrestricted", ack=True)
    with execution_scope(ctx_for(alice["id"], sid)):
        assert "seed" in wr.try_remote("exec_bash", {"script": "echo seed > payload.txt && cat payload.txt"})
        from app.runtime.tools import portal as portal_tool
        out = portal_tool.run({"action": "copy", "src": f"{wid_a}:payload.txt",
                               "dst": f"{wid_b}:incoming.txt"})
        assert "Copied" in out
        # Destination read-back through its own scope.
        back = portal_tool.run({"action": "copy", "src": f"{wid_b}:incoming.txt",
                                "dst": f"{wid_a}:back.txt"})
        assert "Copied" in back
        # Unknown destination denied (never silently routed).
        denied = portal_tool.run({"action": "copy", "src": f"{wid_a}:payload.txt",
                                  "dst": "wp_missing:zz.txt"})
        assert denied.startswith("Error:")
        # Audit captured provenance.
        rows = store.with_db(lambda c: c.execute(
            "SELECT action, actor_id, session_id, destination_id, outcome FROM access_audit "
            "WHERE action='portal.transfer' AND session_id=?", (sid,)).fetchall())
        assert rows and all(dict(r)["actor_id"] == alice["id"] for r in rows)
    # Bob (no grants on either) cannot move anything.
    sid_bob = store.create_swarm_session(["main"], user_id=bob["id"])
    personal = store.access.ensure_personal_space(bob["id"])
    store.access.set_chat_access(bob["id"], sid_bob, personal["id"])
    with execution_scope(ctx_for(bob["id"], sid_bob)):
        from app.runtime.tools import portal as portal_tool2
        assert portal_tool2.run({"action": "copy", "src": f"{wid_a}:payload.txt",
                                 "dst": f"{wid_b}:bob.txt"}).startswith("Error:")


def test_transfer_size_bound_enforced(world):
    alice = world["alice"]
    wid, _ = world["dest"]["wp_a"]
    grant_all(world, alice["id"], wid, unrestricted=True)
    from app.runtime.portal import transfers
    sid = chat(world, alice["id"], wid, mode="unrestricted", ack=True)
    with execution_scope(ctx_for(alice["id"], sid)):
        from app.runtime.tools import workplace_remote as wr
        assert "big" in wr.try_remote(
            "exec_bash", {"script": "head -c 700000 /dev/zero | tr '\\0' Z > big.bin && echo big"})
        from app.runtime.tools import portal as portal_tool
        import app.runtime.portal.io as io
        loc = io.parse_location(f"{wid}:big.bin")
        assert io.stat_size(loc) > transfers.SYNC_MAX_BYTES
        out = portal_tool.run({"action": "copy", "src": f"{wid}:big.bin",
                               "dst": f"{wid}:big2.bin"})
        assert "Started transfer" in out
        job_id = out.split()[2].rstrip(":")
        deadline = time.time() + 10
        while time.time() < deadline:
            status = portal_tool.run({"action": "status", "id": job_id})
            if "status: done" in status:
                break
            time.sleep(0.05)
        assert "status: done" in status
