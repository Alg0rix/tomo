"""SSH exec helpers with mocked Paramiko (no real network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.workplaces import ssh_exec


@pytest.fixture
def ssh_wp() -> dict:
    return {
        "id": "wp_ssh",
        "kind": "ssh",
        "ssh_host": "box.test",
        "ssh_port": 22,
        "ssh_user": "ops",
        "ssh_password": "secret",
        "ssh_key": "",
        "root_path": "/home/ops/work",
    }


def test_exec_bash_formats_result(ssh_wp: dict) -> None:
    client = MagicMock()
    stdin = MagicMock()
    stdout = MagicMock()
    stderr = MagicMock()
    stdout.read.return_value = b"hello\n"
    stderr.read.return_value = b""
    stdout.channel.recv_exit_status.return_value = 0
    client.exec_command.return_value = (stdin, stdout, stderr)

    with patch.object(ssh_exec, "connect", return_value=client):
        out = ssh_exec.exec_bash(ssh_wp, {"script": "echo hello", "timeout": 10})
    assert out["stdout"] == "hello\n"
    assert out["exit_code"] == 0
    client.close.assert_called()


def test_call_unknown_method(ssh_wp: dict) -> None:
    result = ssh_exec.call(ssh_wp, "nope", {})
    assert result["ok"] is False
    assert "unknown" in result["error"]


def test_call_read_write_file_b64(ssh_wp: dict) -> None:
    import base64

    client = MagicMock()
    sftp = MagicMock()
    client.open_sftp.return_value = sftp
    st = MagicMock()
    st.st_size = 5
    sftp.stat.return_value = st
    handle = MagicMock()
    handle.read.return_value = b"hello"
    handle.__enter__ = MagicMock(return_value=handle)
    handle.__exit__ = MagicMock(return_value=False)
    sftp.file.return_value = handle

    with patch.object(ssh_exec, "connect", return_value=client):
        got = ssh_exec.call(
            ssh_wp, "read_file_b64", {"path": "f.bin", "offset": 0, "size": 5}
        )
    assert got["ok"] is True
    assert got["result"]["total_size"] == 5
    assert base64.b64decode(got["result"]["data"]) == b"hello"

    write_handle = MagicMock()
    write_handle.__enter__ = MagicMock(return_value=write_handle)
    write_handle.__exit__ = MagicMock(return_value=False)
    sftp.file.return_value = write_handle
    with patch.object(ssh_exec, "connect", return_value=client):
        got2 = ssh_exec.call(
            ssh_wp,
            "write_file_b64",
            {
                "path": "out.bin",
                "data": base64.b64encode(b"xyz").decode("ascii"),
                "offset": 0,
                "is_last": True,
            },
        )
    assert got2["ok"] is True
    sftp.rename.assert_called()


def test_call_str_replace(ssh_wp: dict) -> None:
    with patch.object(
        ssh_exec, "read_file", return_value={"content": "aa X aa", "path": "/r/f.txt", "size": 7}
    ), patch.object(
        ssh_exec, "write_file", return_value={"ok": True, "path": "/r/f.txt"}
    ):
        result = ssh_exec.call(
            ssh_wp,
            "str_replace",
            {"path": "f.txt", "old_string": "X", "new_string": "Y"},
        )
    assert result["ok"] is True
    assert result["result"]["replacements"] == 1


@pytest.fixture
def local_ssh_jobs(tmp_path, monkeypatch):
    """Execute the exact remote supervisor program locally without SSH/network."""
    import json
    import os
    import subprocess
    import sys

    monkeypatch.setenv("HOME", str(tmp_path))
    workplace = {"id": "test-ssh", "kind": "ssh", "root_path": str(tmp_path)}

    def invoke(wp, operation, params):
        request = dict(params, op=operation, namespace="test", root=wp["root_path"])
        if operation == "start":
            request.setdefault("id", "ssh_" + __import__("uuid").uuid4().hex)
            request["program"] = ssh_exec._SSH_JOBS_PROGRAM
        result = subprocess.run(
            [sys.executable, "-c", ssh_exec._SSH_JOBS_PROGRAM, json.dumps(request)],
            capture_output=True, text=True, timeout=10, env=os.environ.copy(),
        )
        if result.returncode:
            raise ssh_exec.SSHJobRejected(result.stderr)
        return json.loads(result.stdout)

    monkeypatch.setattr(ssh_exec, "_ssh_job_call", invoke)
    return workplace


def _wait_ssh_job(workplace, jid):
    import time

    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        result = ssh_exec.process_status(workplace, {"id": jid})
        if result["status"] not in ("starting", "running", "stopping"):
            return result
        time.sleep(.025)
    pytest.fail("SSH job did not terminate")


def test_ssh_jobs_isolated_and_preserve_exit_code(local_ssh_jobs):
    a = ssh_exec.process_start(local_ssh_jobs, {"command": "sleep .15; printf first; exit 7"})
    b = ssh_exec.process_start(local_ssh_jobs, {"command": "printf second; exit 0"})
    assert a["id"] != b["id"]
    a = _wait_ssh_job(local_ssh_jobs, a["id"])
    b = _wait_ssh_job(local_ssh_jobs, b["id"])
    assert (a["stdout"], a["returncode"]) == ("first", 7)
    assert (b["stdout"], b["returncode"]) == ("second", 0)
    listed = ssh_exec.process_list(local_ssh_jobs, {})
    assert {j["id"] for j in listed} == {a["id"], b["id"]}
    assert ssh_exec.process_kill(local_ssh_jobs, {"id": a["id"]})["returncode"] == 7


def test_ssh_natural_exit_cleans_group_and_retains_real_result(local_ssh_jobs):
    import time
    from pathlib import Path

    job = ssh_exec.process_start(local_ssh_jobs, {
        "command": "(trap '' TERM; sleep 2; echo survived > escaped) & printf final; exit 9"
    })
    result = _wait_ssh_job(local_ssh_jobs, job["id"])
    assert result["status"] == "exited"
    assert result["returncode"] == 9
    assert result["stdout"] == "final"
    time.sleep(2.1)
    assert not (Path(local_ssh_jobs["root_path"]) / "escaped").exists()


def test_ssh_stop_group_does_not_stop_sibling(local_ssh_jobs):
    first = ssh_exec.process_start(local_ssh_jobs, {"command": "sleep 60 & wait"})
    second = ssh_exec.process_start(local_ssh_jobs, {"command": "sleep .5; printf alive"})
    stopped = ssh_exec.process_kill(local_ssh_jobs, {"id": first["id"]})
    assert stopped["status"] == "stopped"
    sibling = _wait_ssh_job(local_ssh_jobs, second["id"])
    assert sibling["returncode"] == 0
    assert sibling["stdout"] == "alive"


def test_ssh_missing_handle_unknown(local_ssh_jobs):
    result = ssh_exec.process_status(local_ssh_jobs, {"id": "ssh_" + "0" * 32})
    assert result["status"] == "unknown"
    assert result["returncode"] is None


def test_ssh_job_cwd_cannot_escape_root(local_ssh_jobs):
    with pytest.raises(ssh_exec.SSHJobRejected):
        ssh_exec.process_start(local_ssh_jobs, {"command": "true", "cwd": "/"})


def test_ssh_verbose_output_flushes_bounded_tail_at_bounded_cadence(local_ssh_jobs, monkeypatch):
    program = ssh_exec._SSH_JOBS_PROGRAM.replace(
        '(job / name).write_bytes(tail)',
        'record["flushes"] = record.get("flushes", 0) + 1; (job / name).write_bytes(tail)')
    monkeypatch.setattr(ssh_exec, '_SSH_JOBS_PROGRAM', program)
    job = ssh_exec.process_start(local_ssh_jobs, {'command': "python3 -c 'import os,time; os.write(1,b\"x\"*1500000); time.sleep(.3); print(\"tail\",flush=True)'"})
    result = _wait_ssh_job(local_ssh_jobs, job['id'])
    assert result['returncode'] == 0 and result['truncated']
    assert len(result['stdout'].encode()) <= 512 * 1024
    assert result['stdout'].endswith('tail\n') and result['flushes'] < 20


def test_ssh_unknown_stop_checks_identity_and_preserves_unknown_exit_code(local_ssh_jobs):
    import json
    import os
    import signal
    import time
    from pathlib import Path
    from app.services.background_jobs import _group_alive

    job = ssh_exec.process_start(local_ssh_jobs, {'command': 'sleep 60'})
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        record = ssh_exec.process_status(local_ssh_jobs, {'id': job['id']})
        if record.get('pid_identity'):
            break
        time.sleep(.02)
    assert record.get('pid_identity')
    os.kill(record['supervisor_pid'], signal.SIGKILL)
    path = Path(local_ssh_jobs['root_path']) / '.cache/tomo-jobs/test' / job['id'] / 'meta.json'
    original = json.loads(path.read_text())
    try:
        path.write_text(json.dumps({**original, 'pid_identity': 'wrong process signature'}))
        result = ssh_exec.process_kill(local_ssh_jobs, {'id': job['id']})
        assert result['status'] == 'unknown' and _group_alive(record['pid'])
        path.write_text(json.dumps(original))
        result = ssh_exec.process_kill(local_ssh_jobs, {'id': job['id']})
        assert result['status'] == 'unknown' and result['returncode'] is None
        assert result['stop_confirmed'] and not _group_alive(record['pid'])
    finally:
        if _group_alive(record['pid']):
            os.killpg(record['pid'], signal.SIGKILL)


def test_ssh_workplace_root_edit_keeps_monitor_namespace(ssh_wp):
    import json
    import shlex

    client = MagicMock()
    stdout, stderr = MagicMock(), MagicMock()
    stdout.read.return_value = b'{"process_contract":1}'
    stdout.channel.recv_exit_status.return_value = 0
    stderr.read.return_value = b''
    client.exec_command.return_value = (MagicMock(), stdout, stderr)
    with patch.object(ssh_exec, 'connect', return_value=client):
        ssh_exec.process_status(ssh_wp, {'id': '__contract__'})
        ssh_exec.process_status({**ssh_wp, 'root_path': '/different/root'}, {'id': '__contract__'})
    requests = [json.loads(shlex.split(call.args[0])[-1]) for call in client.exec_command.call_args_list]
    assert requests[0]['namespace'] == requests[1]['namespace']
