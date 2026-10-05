"""Real SQLite + real Docker boundary acceptance (no mocked CLI isolation).

Build Dockerfile.sandbox first. Runtime/image-dependent tests skip explicitly
when unavailable; policy and fail-closed tests still run with real app stores.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import pytest

from app.runtime.access import AccessDenied, AccessUnavailable, execution_scope
from app.runtime.isolation.backend import ContainerBackend
from app.runtime.isolation import tool_dispatch
from app.runtime.tools import bash, delete_file, list_dir, patch, read_file, runpy, search_files, str_replace, write_file
from app.services.store import Store


@pytest.fixture
def setup(tmp_path):
    db = Store(tmp_path / "isolation.db")
    alice = db.create_user({"username": "alice", "password": "password123"})["id"]
    bob = db.create_user({"username": "bob", "password": "password123"})["id"]
    profile = db.create_llm_profile({"name": "Sandbox model", "model": "test-model", "api_key": "SERVER_SECRET_NOT_IN_SANDBOX"})
    db.set_default_llm_profile(profile["id"])
    for user in (alice, bob):
        db.access.assign("usr_admin", user, "model", profile["id"])
    alice_sid = db.create_home_session(alice)["session_id"]
    bob_sid = db.create_home_session(bob)["session_id"]
    yield db, alice, bob, alice_sid, bob_sid
    # OS teardown can finish before a cancelled job publishes its final audit.
    # Serialize SQLite close with those callbacks, just like Store.rebind does.
    db.with_db(lambda conn: conn.close())


def instance(db, image="tomo:sandbox"):
    return ContainerBackend(policy=db.access, image=image, namespace="test-" + uuid.uuid4().hex)


def test_confirmed_idle_namespace_can_revoke_without_runtime(setup):
    db, alice, bob, sid, bob_sid = setup
    broker = instance(db)
    broker._recover_namespace()  # Real runtime proves this unique namespace empty.
    broker.runtime = '/missing-runtime'
    db.access.register_execution_stopper(broker.stop_session)
    profile = db.access.list_visible_models(alice)[0]['id']
    db.access.revoke('usr_admin', alice, 'model', profile)
    assert not db.get_session(sid)['access_pending']
    assert not db.access.can_use(alice, 'model', profile)
    with pytest.raises(AccessDenied):
        db.access.resolve_context(alice, sid)
    # Missing runtime still cannot admit restricted execution: idle proof is
    # permission to skip unnecessary teardown, not a host fallback.
    db.access.assign('usr_admin', alice, 'model', profile)
    ctx = db.access.resolve_context(alice, sid)
    with pytest.raises(AccessUnavailable):
        broker.execute(ctx, ['sh', '-c', 'echo must-not-run'])
    broker.close()


def test_no_identity_unavailable_image_and_private_override_never_execute_host(setup, monkeypatch, tmp_path):
    db, alice, bob, sid, bob_sid = setup
    broker = instance(db, image="tomo:missing-isolation-" + uuid.uuid4().hex)
    monkeypatch.setattr(tool_dispatch, "backend", broker)
    marker = tmp_path / "must-not-exist"
    command = f"touch {marker}"
    assert "identity" in bash.run({"command": command})
    ctx = db.access.resolve_context(alice, sid)
    with execution_scope(ctx):
        assert bash.run({"command": command}).startswith("Error:")
        assert runpy.run({"code": f"open({str(marker)!r}, 'w').write('escaped')"}).startswith("Error:")
        assert write_file.run({"path": str(marker), "content": "escaped"}).startswith("Error:")
        bob_resource = db.get_session(bob_sid)["workplace_id"]
        assert "outside" in bash.run({"command": command, "workplace_id": bob_resource})
    assert not marker.exists()
    with pytest.raises(AccessDenied):
        db.access.authorize_resource(ctx, bob_resource)
    broker.close()


@pytest.fixture
def real(setup, monkeypatch):
    db, alice, bob, sid, bob_sid = setup
    runtime = os.environ.get("TOMO_SANDBOX_RUNTIME", "docker")
    image = os.environ.get("TOMO_SANDBOX_IMAGE", "tomo:sandbox")
    try:
        subprocess.run([runtime, "info"], capture_output=True, check=True, timeout=10)
        subprocess.run([runtime, "image", "inspect", image], capture_output=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("REAL container acceptance not verified: local runtime or Dockerfile.sandbox image unavailable")
    broker = ContainerBackend(policy=db.access, runtime=runtime, image=image, namespace="test-" + uuid.uuid4().hex)
    monkeypatch.setenv("SERVER_SECRET", "server-only-value-must-not-enter-container")
    db.access.register_execution_stopper(broker.stop_session)
    # Tests use an ordinary temporary filesystem. Its ENTIRE hard capacity is
    # charged, not du polling. Production uses bounded managed filesystems.
    fs = os.statvfs(db.get_workplace(db.get_session(sid)["workplace_id"])["root_path"])
    capacity_mb = (fs.f_blocks * fs.f_frsize // (1024 * 1024)) + 4096
    for user in (alice, bob):
        db.access.set_quota("usr_admin", user, {"disk_mb": capacity_mb, "memory_mb": 2048})
    monkeypatch.setattr(tool_dispatch, "backend", broker)
    yield broker, setup
    broker.close()


def test_durable_admission_prevents_false_idle_after_broker_restart(real):
    broker, (db, alice, bob, sid, bob_sid) = real
    ctx = db.access.resolve_context(alice, sid)
    broker.hold(ctx)
    # A new supervisor has no in-memory handles. Persisted uncertainty must
    # still force actual runtime teardown, even after a clean-namespace proof.
    restarted = ContainerBackend(policy=db.access, runtime='/missing-runtime')
    restarted.namespace = broker.namespace
    try:
        with pytest.raises(AccessUnavailable):
            restarted.stop_session(sid)
        assert db.with_db(lambda c: c.execute(
            'SELECT pending FROM container_admissions WHERE namespace=? AND session_id=?',
            (broker.namespace, sid),
        ).fetchone())[0] == 1
        broker.stop_session(sid)
        restarted.stop_session(sid)  # Actual teardown now gives durable proof.
    finally:
        broker.stop_session(sid)
        restarted.close()


def test_real_file_shell_python_cross_user_readonly_ffmpeg_and_recreation(real):
    broker, (db, alice, bob, sid, bob_sid) = real
    reference = db.access.create_project(bob, "Reference")
    db.access.share_project(bob, reference["id"], alice, "read")
    db.access.set_chat_access(bob, bob_sid, reference["id"])
    owner = db.access.resolve_context(bob, bob_sid)
    result = broker.execute(owner, ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=0.2", "source.wav"])
    assert result.returncode == 0, result.stderr
    reference_root = Path(db.get_workplace(reference["id"])["root_path"])
    (reference_root / "ref.txt").write_text("reference\n")
    # Bob's former personal storage is not enabled in Alice's chat.
    bob_personal = db.access.ensure_personal_space(bob)["id"]
    private = Path(db.get_workplace(bob_personal)["root_path"])
    (private / "bob-secret").write_text("BOB_PRIVATE_VALUE")
    before = db.access.resolve_context(alice, sid)
    denied = broker.execute(before, ["bash", "-lc", f"cat /workplaces/{reference['id']}/ref.txt"])
    assert denied.returncode != 0
    db.access.set_chat_access(alice, sid, before.active_workplace_id, [reference["id"]])
    context = db.access.resolve_context(alice, sid)
    ref = f"/workplaces/{reference['id']}"
    with execution_scope(context):
        assert not write_file.run({"path": "persistent.txt", "content": "one\n"}).startswith("Error")
        assert "one" in read_file.run({"path": "persistent.txt"})
        target = Path(context.resources[0].root_path) / "persistent.txt"
        with target.open("r+") as competing_editor:
            fcntl.flock(competing_editor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert "Concurrent edit" in str_replace.run({"path": "persistent.txt", "old_string": "one", "new_string": "lost"})
        assert "Replaced" in str_replace.run({"path": "persistent.txt", "old_string": "one", "new_string": "two"})
        assert "Applied" in patch.run({"path": "persistent.txt", "patch": "@@ -1 +1 @@\n-two\n+three\n"})
        assert "persistent.txt" in list_dir.run({})
        assert "three" in search_files.run({"pattern": "three"})
        assert "reference" in read_file.run({"path": ref + "/ref.txt"})
        assert "Error" in write_file.run({"path": ref + "/ref.txt", "content": "destroyed"})
        assert "Error" in delete_file.run({"path": ref + "/ref.txt"})
        assert "Error" in str_replace.run({"path": ref + "/ref.txt", "old_string": "reference", "new_string": "bad"})
        assert "Error" in patch.run({"path": ref + "/ref.txt", "patch": "@@ -1 +1 @@\n-reference\n+bad\n"})
        shell = bash.run({"command": f"ln -s {ref} alias; echo bad > alias/ref.txt"})
        assert "exit code:" in shell
        python = runpy.run({"code": f"open({ref + '/ref.txt'!r}, 'w').write('bad')"})
        assert "exit code:" in python
        output = bash.run({"command": f"ffmpeg -v error -i {ref}/source.wav -y output.mp3; test -s output.mp3"})
        assert output == "(no output)", output
        checks = bash.run({"command": f"test ! -e {private}/bob-secret && test ! -e /workplaces/{bob_personal} && test ! -e {db._path} && test ! -e /var/run/docker.sock && test ! -d /data && test -z \"$TOMO_HOME\" && test -z \"$SERVER_SECRET\" && id -u && python -c 'import os; assert os.getuid() != 0'"})
        assert "exit code:" not in checks, checks
        assert "Deleted" in delete_file.run({"path": "persistent.txt"})
    assert (reference_root / "ref.txt").read_text() == "reference\n"
    # Every action destroyed its environment; project output persisted.
    assert Path(db.get_workplace(context.active_workplace_id)["root_path"], "output.mp3").stat().st_size > 0
    assert broker.execute(context, ["bash", "-lc", "test -s output.mp3 && test ! -e /home/chat/server-secret"]).returncode == 0
    # A host-absolute symlink/argument alias is resolved in the private mount
    # namespace, never reinterpreted against the coordinator filesystem.
    escape = Path(db.get_workplace(context.active_workplace_id)["root_path"]) / "private-alias"
    escape.symlink_to(private / "bob-secret")
    with execution_scope(context):
        assert "BOB_PRIVATE_VALUE" not in read_file.run({"filePath": "private-alias"})
        assert "BOB_PRIVATE_VALUE" not in bash.run({"command": "cat private-alias"})
        assert "exit code:" in runpy.run({"code": "print(open('private-alias').read())"})
    db.access.set_chat_access(alice, sid, context.active_workplace_id)
    fresh = db.access.resolve_context(alice, sid)
    assert broker.execute(fresh, ["bash", "-lc", "cat alias/ref.txt"]).returncode != 0


def test_real_full_toolchain_and_network_none(real):
    broker, (db, alice, bob, sid, bob_sid) = real
    context = db.access.resolve_context(alice, sid)
    result = broker.execute(context, ["bash", "-lc", "for x in ffmpeg convert soffice pdftotext pandoc tesseract node npm uv git ssh rg jq sqlite3; do command -v \"$x\" || exit 1; done; test \"$(awk '/CapEff/ {print $2}' /proc/self/status)\" = 0000000000000000; test \"$(cat /proc/1/comm)\" = sleep"])
    assert result.returncode == 0, result.stderr
    script = """import socket
import docx, pptx, openpyxl, pandas, matplotlib, pypdf, fitz, yt_dlp
try:
    socket.create_connection(('1.1.1.1', 80), timeout=0.2)
except OSError:
    pass
else:
    raise AssertionError('network escape')
import subprocess
import cloakbrowser
rendered = subprocess.run(['cloak-chromium', '--headless', '--no-sandbox', '--disable-gpu',
                           '--disable-dev-shm-usage', '--dump-dom',
                           'data:text/html,<title>Isolated browser</title><p>rendered</p>'],
                          capture_output=True, text=True, timeout=15, check=True)
assert '<title>Isolated browser</title>' in rendered.stdout
assert '<p>rendered</p>' in rendered.stdout
print('full toolchain and isolated browser verified')
"""
    result = broker.execute(context, ["python", "-"], stdin=script, timeout=40)
    assert result.returncode == 0, result.stderr
    assert "isolated browser verified" in result.stdout


def test_real_kernel_cpu_ram_disk_limits_and_detached_descendant_cleanup(real):
    broker, (db, alice, bob, sid, bob_sid) = real
    context = db.access.resolve_context(alice, sid)
    per_chat_memory = context.quota.memory_mb // context.quota.max_concurrent_jobs
    per_chat_cpu = context.quota.cpu / context.quota.max_concurrent_jobs
    script = f"""import errno, os
from pathlib import Path
assert int(Path('/sys/fs/cgroup/memory.max').read_text()) == {per_chat_memory} * 1024 * 1024
assert int(Path('/sys/fs/cgroup/memory.swap.max').read_text()) == 0
quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
assert int(quota) / int(period) == {per_chat_cpu}
assert int(Path('/sys/fs/cgroup/pids.max').read_text()) == 512
assert 'NoNewPrivs:\\t1' in Path('/proc/self/status').read_text()
for target in ('/etc/sandbox-write', '/sys/fs/cgroup/memory.max'):
    try:
        with open(target, 'w') as f: f.write('escaped')
    except OSError:
        pass
    else:
        raise AssertionError('image/cgroup is writable')
# The kernel tmpfs limit, not periodic du polling, rejects actual allocation.
try:
    with open('/tmp/quota-fill', 'wb', buffering=0) as f:
        for i in range(257): f.write(b'x' * (1024 * 1024))
except OSError as e:
    assert e.errno == errno.ENOSPC, e
else:
    raise AssertionError('scratch disk capacity was not enforced')
Path('/tmp/quota-fill').unlink()
print('kernel limits verified')
"""
    result = broker.execute(context, ["python", "-"], stdin=script, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "kernel limits verified" in result.stdout
    root = Path(context.resources[0].root_path)
    result = broker.execute(context, ["python", "-"], stdin="""import subprocess
subprocess.Popen(['bash', '-c', 'sleep 2; touch detached-leaked'],
                 start_new_session=True, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
print('parent finished')
""")
    assert result.returncode == 0, result.stderr
    time.sleep(2.5)
    assert not (root / "detached-leaked").exists()


def test_real_parallel_chats_keep_home_environment_and_processes_private(real):
    broker, (db, alice, bob, sid, bob_sid) = real
    alice_context = db.access.resolve_context(alice, sid)
    bob_context = db.access.resolve_context(bob, bob_sid)
    root = Path(alice_context.resources[0].root_path)
    results = []
    def alice_work():
        results.append(broker.execute(alice_context, ["bash", "-lc",
                       "echo secret > /home/chat/alice-state; echo secret > /tmp/alice-state; "
                       "touch private-started; CHAT_ONLY_SECRET=alice-env-secret sleep 60"], timeout=60))
    worker = threading.Thread(target=alice_work)
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while not (root / "private-started").exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (root / "private-started").exists(), results
        script = """from pathlib import Path
import os
assert not Path('/home/chat/alice-state').exists()
assert not Path('/tmp/alice-state').exists()
assert not os.environ.get('CHAT_ONLY_SECRET')
for path in Path('/proc').glob('[0-9]*/environ'):
    try: environment = path.read_bytes()
    except OSError: continue
    assert b'alice-env-secret' not in environment
print('private chat environment verified')
"""
        result = broker.execute(bob_context, ["python", "-"], stdin=script)
        assert result.returncode == 0, result.stderr
        assert "private chat environment verified" in result.stdout
    finally:
        broker.stop_session(sid)
        worker.join(10)
    assert not worker.is_alive()


def test_real_grant_revocation_removes_running_mounts_and_denies_stale_work(real):
    broker, (db, alice, bob, sid, bob_sid) = real
    reference = db.access.create_project(bob, "Revocable")
    db.access.share_project(bob, reference["id"], alice, "read_write")
    initial = db.access.resolve_context(alice, sid)
    db.access.set_chat_access(alice, sid, initial.active_workplace_id, [reference["id"]])
    context = db.access.resolve_context(alice, sid)
    root = Path(db.get_workplace(reference["id"])["root_path"])
    results = []
    def work():
        results.append(broker.execute(context, ["bash", "-lc",
                       f"touch /workplaces/{reference['id']}/grant-started; "
                       f"sleep 60; touch /workplaces/{reference['id']}/grant-leaked"], timeout=60))
    worker = threading.Thread(target=work)
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while not (root / "grant-started").exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (root / "grant-started").exists(), results
        assert db.access.revoke(bob, alice, "workplace", reference["id"])["state"] == "revoked"
        worker.join(10)
        assert not worker.is_alive()
        assert not (root / "grant-leaked").exists()
        with pytest.raises(AccessDenied):
            broker.execute(context, ["true"])
        db.access.set_chat_access(alice, sid, context.active_workplace_id)
        fresh = db.access.resolve_context(alice, sid)
        assert broker.execute(fresh, ["test", "!", "-e", f"/workplaces/{reference['id']}"]).returncode == 0
    finally:
        broker.stop_session(sid)
        worker.join(10)


def test_real_revocation_duration_and_aggregate_concurrency(real):
    broker, (db, alice, bob, sid, bob_sid) = real
    db.access.set_quota("usr_admin", alice, {**asdict(db.access.get_quota(alice)), "max_concurrent_jobs": 1})
    context = db.access.resolve_context(alice, sid)
    other_sid = db.create_home_session(alice)["session_id"]
    other = db.access.resolve_context(alice, other_sid)
    marker = Path(context.resources[0].root_path) / "started"
    results = []
    def command():
        try:
            results.append(broker.execute(context, ["bash", "-lc", "touch started; sleep 60; touch leaked"], timeout=60))
        except AccessDenied as exc:
            results.append(exc)
    thread = threading.Thread(target=command)
    thread.start()
    deadline = time.monotonic() + 30
    while not marker.exists() and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), results
    with pytest.raises(AccessUnavailable, match="concurrency"):
        broker.execute(other, ["true"])
    # Policy mutation marks pending, stops and removes live mounts synchronously.
    db.access.set_chat_access(alice, sid, context.active_workplace_id, execution_mode="restricted", additional_workplace_ids=[])
    # Same access is a no-op: force a resource change to exercise stopper.
    project = db.access.create_project(alice, "Next")
    db.access.set_chat_access(alice, sid, project["id"])
    thread.join(10)
    assert not thread.is_alive()
    assert not (marker.parent / "leaked").exists()
    with pytest.raises(AccessDenied):
        broker.execute(context, ["true"])
    fresh = db.access.resolve_context(alice, sid)
    with pytest.raises(AccessUnavailable, match="terminated"):
        broker.execute(fresh, ["sleep", "60"], timeout=0.2)
    assert broker.execute(fresh, ["true"]).returncode == 0


def test_real_background_admission_reserves_quota_before_registering_handle(real, monkeypatch):
    from app.runtime.isolation import jobs

    broker, (db, alice, bob, sid, bob_sid) = real
    monkeypatch.setattr(jobs, "backend", broker)
    db.access.set_quota("usr_admin", alice, {**asdict(db.access.get_quota(alice)), "max_concurrent_jobs": 1})
    context = db.access.resolve_context(alice, sid)
    other = db.access.resolve_context(alice, db.create_home_session(alice)["session_id"])
    # Hold the REAL policy fence so the worker cannot run yet. A registered job
    # must already occupy an enforced admission slot, not merely a Python thread.
    with db.access.execution_guard(context):
        handle = jobs.start(context, "sleep 60", context.resources[0].mount_path)["id"]
        try:
            with pytest.raises(AccessUnavailable, match="concurrency"):
                broker.execute(other, ["true"])
            with pytest.raises(AccessUnavailable, match="managed work"):
                jobs.start(context, "true", context.resources[0].mount_path)
        finally:
            assert jobs.stop(handle)["stop_confirmed"]
    assert broker.execute(other, ["true"]).returncode == 0


def test_real_stopping_completed_job_does_not_kill_later_chat_work(real, monkeypatch):
    from app.runtime.isolation import jobs

    broker, (db, alice, bob, sid, bob_sid) = real
    monkeypatch.setattr(jobs, "backend", broker)
    context = db.access.resolve_context(alice, sid)
    root = Path(context.resources[0].root_path)
    finished = jobs.start(context, "true", context.resources[0].mount_path)["id"]
    deadline = time.monotonic() + 30
    while jobs.observe(finished, context=context)["status"] == "starting" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert jobs.observe(finished, context=context)["status"] == "succeeded"
    live = jobs.start(context, "touch newer-started; sleep 60; touch newer-leaked", context.resources[0].mount_path)["id"]
    try:
        while not (root / "newer-started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (root / "newer-started").exists()
        assert jobs.stop(finished)["status"] == "succeeded"
        assert jobs.observe(live, context=context)["status"] == "starting"
    finally:
        assert jobs.stop(live)["stop_confirmed"]
    assert not (root / "newer-leaked").exists()


def test_remote_restricted_is_capability_error_not_local_fallback(setup, monkeypatch, tmp_path):
    from app.runtime.isolation import host
    from app.services import access as access_module
    import app.services

    db, alice, bob, sid, bob_sid = setup
    broker = instance(db)
    monkeypatch.setattr(access_module, "access", db.access)
    monkeypatch.setattr(app.services, "store", db)
    # Fresh temporary store: no container was ever admitted. Use the REAL
    # process supervisor's empty teardown, so remote policy still tests on
    # machines without Docker (no fake CLI/container containment evidence).
    db.access.register_execution_stopper(host.stop_session)
    remote = db.create_workplace({"name": "Remote", "kind": "ssh", "host": "127.0.0.1", "root_path": "/project"})
    db.update_workplace(remote["id"], {"status": "connected"})
    db.access.assign("usr_admin", alice, "workplace", remote["id"], "read_write")
    db.access.set_chat_access(alice, sid, remote["id"])
    context = db.access.resolve_context(alice, sid)
    monkeypatch.setattr(tool_dispatch, "backend", broker)
    marker = tmp_path / "no-remote-fallback"
    with execution_scope(context):
        # Restricted SSH has no destination-side boundary: fail closed with
        # a destination-specific error, never a local fallback.
        with pytest.raises(AccessUnavailable, match="verified restricted boundary"):
            bash.run({"command": f"touch {marker}"})
        assert write_file.run({"path": str(marker), "content": "bad"}).startswith("Error:")
    assert not marker.exists()
    broker.close()


def test_explicit_unrestricted_remote_background_is_unavailable_without_supervised_contract(setup, monkeypatch):
    from app.services import access as access_module, background_job_backends
    from app.runtime.isolation import host
    import app.services

    db, alice, bob, sid, bob_sid = setup
    monkeypatch.setattr(access_module, "access", db.access)
    monkeypatch.setattr(app.services, "store", db)
    db.access.register_execution_stopper(host.stop_session)
    admin_sid = db.create_home_session("usr_admin")["session_id"]
    remote = db.create_workplace({"name": "Unsupervised SSH", "kind": "ssh", "host": "127.0.0.1", "root_path": "/project"})
    db.update_workplace(remote["id"], {"status": "connected"})
    db.access.assign("usr_admin", "usr_admin", "unrestricted", remote["id"])
    db.access.set_chat_access("usr_admin", admin_sid, remote["id"], execution_mode="unrestricted", unrestricted_acknowledged=True)
    context = db.access.resolve_context("usr_admin", admin_sid)
    with execution_scope(context):
        with pytest.raises(AccessUnavailable, match="duration-bounded supervised boundary"):
            background_job_backends.resolve_backend()
        with pytest.raises(AccessUnavailable, match="duration-bounded supervised boundary"):
            background_job_backends.start_remote("ssh", remote["id"], "touch forbidden", "/project")


def test_explicit_admin_unrestricted_host_remains_usable(setup, monkeypatch, tmp_path):
    from app.services import access as access_module
    from app.runtime.isolation import host

    db, alice, bob, sid, bob_sid = setup
    broker = instance(db, image="tomo:does-not-exist")
    monkeypatch.setattr(tool_dispatch, "backend", broker)
    monkeypatch.setattr(access_module, "access", db.access)
    import app.services

    monkeypatch.setattr(app.services, "store", db)
    db.access.register_execution_stopper(host.stop_session)
    admin_sid = db.create_home_session("usr_admin")["session_id"]
    workplace = db.get_session(admin_sid)["workplace_id"]
    db.access.assign("usr_admin", "usr_admin", "unrestricted", workplace)
    db.access.set_chat_access("usr_admin", admin_sid, workplace, execution_mode="unrestricted", unrestricted_acknowledged=True)
    context = db.access.resolve_context("usr_admin", admin_sid)
    marker = tmp_path / "explicit-admin-host"
    with execution_scope(context):
        assert bash.run({"command": f"printf admin > {marker}"}) == "(no output)"
        assert "admin" in read_file.run({"path": str(marker)})
        assert runpy.run({"code": "print('host python')"}) == "host python"
    assert marker.read_text() == "admin"
    broker.close()


def test_real_background_jobs_preserve_context_and_stop_queued_and_running_work(real, monkeypatch):
    from app.runtime.isolation import jobs

    broker, (db, alice, bob, sid, bob_sid) = real
    monkeypatch.setattr(jobs, "backend", broker)
    db.access.register_execution_stopper(jobs.stop_session)
    context = db.access.resolve_context(alice, sid)
    root = Path(context.resources[0].root_path)
    handle = jobs.start(context, "printf background > done", context.resources[0].mount_path)["id"]
    deadline = time.monotonic() + 30
    while jobs.observe(handle, context=context)["status"] == "starting" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert jobs.observe(handle, context=context)["status"] == "succeeded"
    assert (root / "done").read_text() == "background"
    with pytest.raises(AccessDenied):
        jobs.observe(handle, context=db.access.resolve_context(bob, bob_sid))
    running = jobs.start(context, "touch bg-started; sleep 60; touch bg-leaked", context.resources[0].mount_path)["id"]
    while not (root / "bg-started").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (root / "bg-started").exists()
    assert jobs.stop(running)["stop_confirmed"]
    assert not (root / "bg-leaked").exists()
    # A job queued behind a mutation fence must never start after cancellation.
    with db.access.execution_guard(context):
        queued = jobs.start(context, "touch queued-leaked", context.resources[0].mount_path)["id"]
        assert jobs.stop(queued)["stop_confirmed"]
    time.sleep(0.2)
    assert not (root / "queued-leaked").exists()


def test_unrestricted_file_io_fence_blocks_revocation_until_access_is_released(setup, monkeypatch):
    from app.services import access as access_module
    from app.runtime.isolation import host
    from app.runtime.tools.sandbox import file_execution_guard
    import app.services

    db, alice, bob, sid, bob_sid = setup
    monkeypatch.setattr(access_module, "access", db.access)
    monkeypatch.setattr(app.services, "store", db)
    db.access.register_execution_stopper(host.stop_session)
    admin_sid = db.create_home_session("usr_admin")["session_id"]
    active = db.get_session(admin_sid)["workplace_id"]
    db.access.assign("usr_admin", "usr_admin", "unrestricted", active)
    db.access.set_chat_access("usr_admin", admin_sid, active, execution_mode="unrestricted", unrestricted_acknowledged=True)
    context = db.access.resolve_context("usr_admin", admin_sid)
    next_project = db.access.create_project("usr_admin", "After revoke")
    started, mutated = threading.Event(), threading.Event()
    failures = []
    def mutate():
        started.set()
        try:
            db.access.set_chat_access("usr_admin", admin_sid, next_project["id"])
        except Exception as exc:
            failures.append(exc)
        finally:
            mutated.set()
    with execution_scope(context):
        with file_execution_guard():
            worker = threading.Thread(target=mutate)
            worker.start()
            assert started.wait(5)
            assert not mutated.wait(0.2)
            assert "Created" in write_file.run({"path": "before-revocation", "content": "authorized"})
            assert "authorized" in read_file.run({"path": "before-revocation"})
        worker.join(10)
        assert mutated.is_set() and not failures
        assert write_file.run({"path": "after-revocation", "content": "bad"}).startswith("Error:")
    assert not Path(context.resources[0].root_path, "after-revocation").exists()


def test_default_disk_quota_fails_closed_on_unbounded_managed_storage(setup):
    db, alice, bob, sid, bob_sid = setup
    broker = instance(db)
    try:
        subprocess.run(["docker", "image", "inspect", "tomo:sandbox"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Real runtime/image required to verify disk admission")
    with pytest.raises(AccessUnavailable, match="disk capacity"):
        broker.execute(db.access.resolve_context(alice, sid), ["true"])
    broker.close()


def test_owned_upload_import_and_document_conversion_use_actual_container(real, monkeypatch):
    from app.core.config import TOMO_HOME
    from app.runtime.isolation import attachments
    import base64

    broker, (db, alice, bob, sid, bob_sid) = real
    monkeypatch.setattr(attachments, "backend", broker)
    directory = Path(TOMO_HOME) / "attachments" / sid
    directory.mkdir(parents=True, exist_ok=True)
    context = db.access.resolve_context(alice, sid)
    generated = broker.execute(context, ["python", "-c",
        "import pymupdf,base64; d=pymupdf.open(); p=d.new_page(); "
        "p.insert_text((72,72),'isolated document evidence'); print(base64.b64encode(d.tobytes()).decode())"])
    assert generated.returncode == 0, generated.stderr
    payload = base64.b64decode(generated.stdout.strip(), validate=True)
    assert payload.startswith(b"%PDF"), repr(payload[:64])
    path = directory / "att_evidence.pdf"
    path.write_bytes(payload)
    att = db.create_attachment(attachment_id="att_evidence", session_id=sid,
                               filename=path.name, original_name="evidence.pdf",
                               mime_type="application/pdf", size_bytes=len(payload), file_path=str(path))
    with execution_scope(context):
        destination = attachments.import_upload(att)
        assert destination.startswith('/workplaces/' + context.active_workplace_id)
        result = broker.execute(context, ["python", "-c", "import sys; assert open(sys.argv[1],'rb').read().startswith(b'%PDF')", destination])
        assert result.returncode == 0, result.stderr
        text = attachments.convert_document(att)
        assert "isolated document evidence" in text
    with execution_scope(db.access.resolve_context(bob, bob_sid)):
        with pytest.raises(AccessDenied, match="owned chat"):
            attachments.read_owned_upload(att)
    # Neither host alias nor explicitly unrestricted mode can launch a parser.
    path.unlink()
    path.symlink_to(db._path)
    with execution_scope(context):
        with pytest.raises(AccessDenied, match="unavailable"):
            attachments.convert_document(att)
    db.access.assign("usr_admin", alice, "unrestricted", context.active_workplace_id)
    db.access.set_chat_access(alice, sid, context.active_workplace_id,
                             execution_mode="unrestricted", unrestricted_acknowledged=True)
    with execution_scope(db.access.resolve_context(alice, sid)):
        with pytest.raises(AccessUnavailable, match="restricted local container"):
            attachments.convert_document(att)


def test_startup_recovers_orphan_before_rejecting_unavailable_image(real):
    broker, (db, alice, bob, sid, bob_sid) = real
    recovery = instance(db, image='tomo:missing-startup-' + uuid.uuid4().hex)
    label = 'org.tomo.sandbox.namespace=' + recovery.namespace
    # Operator-side simulation of a previous coordinator's retained container.
    orphan = subprocess.run(['docker', 'run', '-d', '--network', 'none', '--cap-drop', 'ALL',
                             '--label', label, '--entrypoint', '/bin/sleep', 'tomo:sandbox', '300'],
                            capture_output=True, text=True, check=True).stdout.strip()
    try:
        with pytest.raises(AccessUnavailable):
            recovery.startup()
        remaining = subprocess.run(['docker', 'ps', '-aq', '--filter', 'label=' + label],
                                   capture_output=True, text=True, check=True).stdout.strip()
        assert not remaining
    finally:
        subprocess.run(['docker', 'rm', '-f', orphan], capture_output=True)
        recovery.close()
