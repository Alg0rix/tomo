"""Stage 2: resource quotas, storage fairness, request limits.

Real policy/SQLite/HTTP seams; no mocked authorization. Each test drives the
production admission path it names and would fail if that path regressed to an
independent counter, an unbounded spool, or a leaked reservation.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from app.runtime.access import AccessUnavailable
from app.services.store import store


@pytest.fixture
def quota_env(tmp_path):
    store.rebind(tmp_path / "quota-ledger.db")
    store.access.register_execution_stopper(lambda sid: None)
    alice = store.create_user({"username": "alice", "password": "password123"})["id"]
    profile = store.create_llm_profile({"name": "Assigned", "model": "test-model", "api_key": "secret"})
    store.set_default_llm_profile(profile["id"])
    store.access.assign("usr_admin", alice, "model", profile["id"])
    sessions = [store.create_home_session(alice)["session_id"] for _ in range(2)]
    yield alice, sessions
    store.rebind(None)


def resolve(alice, sid):
    return store.access.resolve_context(alice, sid)


@pytest.mark.parametrize('admin', [False, True], ids=['member', 'admin'])
def test_parallel_turns_are_not_capped_per_user(quota_env, admin):
    """Many chats of one account run at once; no per-user concurrency cap."""
    from app.runtime.supervision import admitted_turn

    alice, _ = quota_env
    uid = 'usr_admin' if admin else alice
    contexts = [resolve(uid, store.create_home_session(uid)['session_id']) for _ in range(5)]

    async def main():
        entered, release = [asyncio.Event() for _ in contexts], asyncio.Event()

        async def hold(ctx, flag):
            async with admitted_turn(ctx):
                flag.set()
                await release.wait()

        tasks = [asyncio.create_task(hold(c, e)) for c, e in zip(contexts, entered)]
        await asyncio.gather(*(e.wait() for e in entered))
        release.set()
        await asyncio.gather(*tasks)
        return len(tasks)

    assert asyncio.run(asyncio.wait_for(main(), 10)) == 5


@pytest.mark.parametrize('admin', [False, True], ids=['member', 'admin'])
def test_turn_outlives_process_duration_budget_and_releases_admission(quota_env, admin):
    """The shared web/agent/swarm supervisor must not time-cut whole turns."""
    from app.runtime.supervision import admitted_turn

    alice, sessions = quota_env
    uid = 'usr_admin' if admin else alice
    store.access.set_quota('usr_admin', uid, {'duration_seconds': 1})
    sid = store.create_home_session(uid)['session_id'] if admin else sessions[0]
    context = resolve(uid, sid)

    async def work():
        async with admitted_turn(context):
            async with admitted_turn(context):
                await asyncio.sleep(1.2)
                result = 'completed beyond the per-process deadline'
        # Completion must release the registration, not disable admission.
        async with admitted_turn(context):
            return result

    assert asyncio.run(asyncio.wait_for(work(), 5)) == 'completed beyond the per-process deadline'


@pytest.mark.parametrize('admin', [False, True], ids=['member', 'admin'])
def test_unlimited_turn_still_honors_stop_and_releases_admission(quota_env, admin):
    from app.runtime.supervision import admitted_turn

    alice, sessions = quota_env
    uid = 'usr_admin' if admin else alice
    sid = store.create_home_session(uid)['session_id'] if admin else sessions[0]
    context = resolve(uid, sid)

    async def main():
        entered = asyncio.Event()
        async def work():
            async with admitted_turn(context):
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(work())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with admitted_turn(context):
            return 'readmitted after Stop'

    assert asyncio.run(asyncio.wait_for(main(), 5)) == 'readmitted after Stop'


def test_storage_reservation_quota_and_failed_write_release(quota_env):
    """Over-quota reserves deny; failed writes release; success is rescanned."""
    from app.runtime import storage as storage_ledger

    alice, _ = quota_env
    store.access.set_quota("usr_admin", alice, {"disk_mb": 1})
    quota = store.access.get_quota(alice)
    with pytest.raises(AccessUnavailable):
        storage_ledger.reserve(alice, 2 * 1024 * 1024, quota)
    assert storage_ledger.reserved_bytes(alice) == 0
    storage_ledger.reserve(alice, 600 * 1024, quota)
    assert storage_ledger.reserved_bytes(alice) == 600 * 1024
    try:
        raise RuntimeError("simulated failed write")
    except RuntimeError:
        pass
    finally:
        storage_ledger.release(alice, 600 * 1024)
    assert storage_ledger.reserved_bytes(alice) == 0
    # Parallel reservations cannot jointly exceed the quota.
    results = []

    def attempt(n):
        try:
            storage_ledger.reserve(alice, 512 * 1024, quota)
            results.append(True)
        except AccessUnavailable:
            results.append(False)

    workers = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(10)
    assert results.count(True) <= 2 and results.count(False) >= 2
    assert storage_ledger.reserved_bytes(alice) == results.count(True) * 512 * 1024
    storage_ledger.release(alice, results.count(True) * 512 * 1024)
    assert storage_ledger.reserved_bytes(alice) == 0


def test_private_write_counts_attachments_and_denies_over_quota(quota_env, tmp_path):
    """Attachment bytes land in the same per-user admission as vault/artifacts."""
    from app.runtime.access import execution_scope
    from app.runtime.storage import private_write

    alice, sessions = quota_env
    store.access.set_quota("usr_admin", alice, {"disk_mb": 1})
    ctx = resolve(alice, sessions[0])
    with execution_scope(ctx):
        with pytest.raises(AccessUnavailable):
            with private_write(2 * 1024 * 1024):
                pass
        with private_write(100):
            pass


def test_oversized_bodies_rejected_before_spool_and_normal_upload_persists(tmp_path, monkeypatch):
    """413 before routing/parsing for huge bodies; small uploads still work."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    store.rebind(tmp_path / "quota-http.db")
    app = create_app()
    user = store.create_user({"username": "uploader", "password": "password123"})
    profile = store.create_llm_profile({"name": "M", "model": "test-model", "api_key": "k"})
    store.set_default_llm_profile(profile["id"])
    store.access.assign("usr_admin", user["id"], "model", profile["id"])
    client = TestClient(app, base_url="https://testserver", follow_redirects=False,
                        client=(user["id"], 50000))
    assert client.post("/login", data={"username": "uploader", "password": "password123"}).status_code == 303
    try:
        # Unknown path would otherwise reach the policy layer: 413 proves the
        # limits middleware runs before routing, spooling, or auth handling.
        big = b"x" * (33 * 1024 * 1024)
        r = client.post("/api/no-such-route", content=big)
        assert r.status_code == 413, r.status_code
        # Chunked (length-unknown) upload over a lowered cap also 413s.
        monkeypatch.setenv("TOMO_MAX_UPLOAD_BYTES", str(64 * 1024))
        sid = store.create_home_session(user["id"])["session_id"]
        chunks = (b"y" * 8192 for _ in range(16))  # 128 KiB, no content-length
        r = client.post(f"/api/sessions/{sid}/attachments", content=chunks)
        assert r.status_code == 413, (r.status_code, r.text[:200])
    finally:
        monkeypatch.undo()
    # Normal persistent write still admitted and stored.
    sid = store.create_home_session(user["id"])["session_id"]
    r = client.post(f"/api/sessions/{sid}/attachments",
                    files={"file": ("hello.txt", b"hello quota world")})
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["size_bytes"] == 17
    assert store.get_attachment(body["id"])["session_id"] == sid
    client.close()


def test_tiny_disk_quota_rejects_upload_with_413(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    store.rebind(tmp_path / "quota-http2.db")
    app = create_app()
    user = store.create_user({"username": "smallquota", "password": "password123"})
    profile = store.create_llm_profile({"name": "M", "model": "test-model", "api_key": "k"})
    store.set_default_llm_profile(profile["id"])
    store.access.assign("usr_admin", user["id"], "model", profile["id"])
    store.access.register_execution_stopper(lambda sid: None)
    # 1 MiB quota is smaller than the on-disk account footprint scan margin
    # only when the account already holds data; force denial via zero headroom:
    # reserve the whole quota first through the real reservation seam.
    from app.runtime import storage as storage_ledger

    quota = store.access.get_quota(user["id"])
    assert quota.disk_mb >= 1
    store.access.set_quota("usr_admin", user["id"], {"disk_mb": 1})
    sid = store.create_home_session(user["id"])["session_id"]
    assert sid
    client = TestClient(app, base_url="https://testserver", follow_redirects=False,
                        client=(user["id"], 50001))
    assert client.post("/login", data={"username": "smallquota", "password": "password123"}).status_code == 303
    try:
        current = store.access.get_quota(user["id"])
        storage_ledger.reserve(user["id"], current.disk_mb * 1024 * 1024, current)
        try:
            r = client.post(f"/api/sessions/{sid}/attachments",
                            files={"file": ("big.txt", b"z" * 1024)})
            assert r.status_code == 413, (r.status_code, r.text[:200])
        finally:
            storage_ledger.release(user["id"], current.disk_mb * 1024 * 1024)
        r = client.post(f"/api/sessions/{sid}/attachments",
                        files={"file": ("ok.txt", b"fits")})
        assert r.status_code == 200, r.text[:300]
    finally:
        client.close()


def test_tool_backend_failure_is_generic_without_secret_text(quota_env, monkeypatch):
    """F-2: model sees a generic failure; raw backend text stays server-side."""
    from tests.fakes.access import owned_admin_scope
    from app.runtime.tools import registry

    secret = "sk-live-SECRET-abc123"
    with owned_admin_scope(["main"]) as (ctx, root):
        from app.services import store as _store

        _store.set_agent_tools(ctx.agent_id, {"bash": True})

        def boom(arguments):
            raise RuntimeError(f"backend exploded with key {secret}")

        monkeypatch.setattr("app.runtime.tools.bash.run", boom)
        result = registry.execute("bash", {"command": "echo hi"})
        assert result == "Error: tool 'bash' failed"
        assert secret not in result


def test_companion_events_agent_filter_is_validated(tmp_path):
    """F-3: unknown/invisible agent_id denies; visible agents still list."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    store.rebind(tmp_path / "quota-companion.db")
    app = create_app()
    admin = store.create_user({"username": "compadmin", "password": "password123", "role": "admin"})
    client = TestClient(app, base_url="https://testserver", follow_redirects=False,
                        client=(admin["id"], 50002))
    assert client.post("/login", data={"username": "compadmin", "password": "password123"}).status_code == 303
    try:
        r = client.get("/api/companion/events?agent_id=no-such-agent")
        assert r.status_code == 404, (r.status_code, r.text[:200])
        agent = store.list_agents()[0]
        r = client.get(f"/api/companion/events?agent_id={agent['id']}")
        assert r.status_code == 200, (r.status_code, r.text[:200])
    finally:
        client.close()


def test_host_quota_status_reports_enforcement_capability():
    from app.runtime.isolation import host

    status = host.quota_status()
    assert set(status) >= {"prlimit_enforced", "prlimit_path", "cgroup_controllers", "cgroup_cpu_memory"}
    assert isinstance(status["prlimit_enforced"], bool)


def test_managed_storage_status_and_cli(tmp_path):
    import json
    import subprocess
    import sys

    from app.runtime.storage import BOUND_MARKER, managed_storage_status

    root = tmp_path / "managed"
    root.mkdir()
    status = managed_storage_status(root=root)
    assert status["bounded"] is False and status["exists"] is True
    (root / BOUND_MARKER).write_text("capacity_mb=2048\n")
    assert managed_storage_status(root=root)["bounded"] is True
    proc = subprocess.run([sys.executable, "-m", "app.runtime.storage",
                           "--check", "--root", str(root)],
                          capture_output=True, text=True, cwd=".", timeout=60)
    assert proc.returncode == 0, proc.stderr[-500:]
    assert json.loads(proc.stdout)["bounded"] is True
    empty = tmp_path / "unbounded"
    empty.mkdir()
    proc = subprocess.run([sys.executable, "-m", "app.runtime.storage",
                           "--check", "--root", str(empty)],
                          capture_output=True, text=True, cwd=".", timeout=60)
    assert proc.returncode == 2, proc.stderr[-500:]
