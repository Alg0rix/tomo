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
