"""Install-via-SSH flow with mocked Paramiko and a fake store (no network)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.workplaces import install_via_ssh as mod


class FakeStore:
    """Lightweight in-memory store implementing the calls we use."""

    def __init__(self) -> None:
        self.workplaces: dict[str, dict] = {}
        self.connected_at: dict[str, float] = {}
        self.counter = 0

    def create_workplace(self, data: dict) -> dict:
        self.counter += 1
        wid = f"tun_{self.counter}"
        wp = {"id": wid, "name": data["name"], "kind": "tunnel", **data}
        wp["online"] = False
        wp["status"] = "pairing"
        self.workplaces[wid] = wp
        return wp

    def issue_pairing_code(self, wid: str) -> dict | None:
        wp = self.workplaces.get(wid)
        if not wp:
            return None
        wp.setdefault("pairing_code", "ABCD-EFGH")
        wp["pairing_expires_at"] = time.time() + 600
        return wp

    def get_workplace(self, wid: str) -> dict | None:
        wp = dict(self.workplaces.get(wid) or {})
        if not wp:
            return None
        # Simulate a live connector: nonzero connected_at once online.
        wp["connector_connected_at"] = self.connected_at.get(wid, time.time())
        return wp


def _mock_client(script_out: dict[str, bytes], rc: int = 0) -> MagicMock:
    """SSH client whose exec_command returns per-script stdout (rc by default 0)."""
    conn = MagicMock()

    def exec_command(script: str, timeout: float = 60.0):
        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        for needle, out in script_out.items():
            if needle in script:
                stdout.read.return_value = out
                break
        else:
            stdout.read.return_value = b"ok\n"
        stdout.channel.recv_exit_status.return_value = rc
        stderr.read.return_value = b""
        return stdin, stdout, stderr

    conn.exec_command.side_effect = exec_command
    return conn


def test_install_full_flow() -> None:
    store = FakeStore()
    conn = _mock_client(
        {
            "uname -s; uname -m": b"Linux\nx86_64\n",
            "systemctl": b"active\n",
            "tomo-connector pair": b"paired\n",
        }
    )
    with patch.object(mod.ssh_exec, "connect", return_value=conn):
        result = mod.install_via_ssh(
            ssh_host="box.test",
            ssh_port=22,
            ssh_user="ops",
            ssh_password="pw",
            ssh_key="",
            name="edge",
            server_url="http://tomo.local:8080",
            poll_timeout=0.05,
            poll_interval=0.01,
            now=time.time,
            is_online=lambda wid: False,  # connector stays offline
            store=store,
        )
    assert result.status == "pairing"
    assert result.workplace["kind"] == "tunnel"
    joined = "\n".join(result.log)
    assert "edge" in joined or result.workplace["name"] == "edge"
    assert result.log[-1] == "done"
    conn.close.assert_called()


def test_install_connected_when_online() -> None:
    store = FakeStore()
    conn = _mock_client(
        {
            "uname -s; uname -m": b"Linux\nx86_64\n",
            "systemctl": b"active\n",
            "tomo-connector pair": b"paired\n",
        }
    )
    # Connector reports online via the hub lookup we inject.
    with patch.object(mod.ssh_exec, "connect", return_value=conn):
        result = mod.install_via_ssh(
            ssh_host="h",
            ssh_port=22,
            ssh_user="u",
            ssh_password="p",
            ssh_key="",
            name="n",
            server_url="http://s:8080",
            poll_timeout=1.0,
            poll_interval=0.01,
            now=time.time,
            is_online=lambda wid: True,
            store=store,
        )
    assert result.status == "connected"
    assert result.log[-2] == "✓ connector online"


def test_install_failure_raises_install_error() -> None:
    """A failed remote download raises InstallError with stage info."""
    store = FakeStore()
    # Download script fails via curl rc=22.
    conn = _mock_client(
        {
            "uname -s; uname -m": b"Linux\nx86_64\n",
            "curl": b"",
        },
        rc=0,
    )
    # Override the download step to actually return rc!=0.
    original = conn.exec_command.side_effect

    def exec_command(script: str, timeout: float = 60.0):
        stdin, stdout, stderr = original(script, timeout)
        if "curl -fsSL" in script:
            stdout.read.return_value = b"curl: (22) The requested URL returned error: 404\n"
            stdout.channel.recv_exit_status.return_value = 22
        return stdin, stdout, stderr

    conn.exec_command.side_effect = exec_command
    with patch.object(mod.ssh_exec, "connect", return_value=conn):
        with pytest.raises(mod.InstallError) as ei:
            mod.install_via_ssh(
                ssh_host="h",
                ssh_port=22,
                ssh_user="u",
                ssh_password="p",
                ssh_key="",
                name="n",
                server_url="http://s:8080",
                store=store,
                poll_timeout=0.01,
                poll_interval=0.01,
            )
    assert ei.value.stage == "download"
    assert ei.value.retryable is True


def test_ssh_connect_failure() -> None:
    store = FakeStore()
    with patch.object(mod.ssh_exec, "connect", side_effect=OSError("no route")):
        with pytest.raises(mod.InstallError) as ei:
            mod.install_via_ssh(
                ssh_host="10.0.0.1",
                ssh_port=22,
                ssh_user="u",
                ssh_password="p",
                ssh_key="",
                name="n",
                server_url="http://s:8080",
                store=store,
            )
    assert ei.value.stage == "ssh"


def test_download_script_contains_expected_url() -> None:
    script = mod._download_script(
        "linux amd64",
        "/home/u/.local/bin/tomo-connector",
        server_url="http://tomo:8080",
        version="v1.2.3",
        verify=True,
    )
    assert "tomo-connector-linux-amd64" in script
    assert "v1.2.3" in script
    assert "SHA256SUMS" in script
    assert "sha256sum" in script
    assert "mv -f" in script
    assert "tomo-connector" in script and "$HOME/.local/bin" in script


def test_download_script_latest_no_checksum_when_verify_off() -> None:
    script = mod._download_script(
        "darwin arm64",
        "/Users/u/.local/bin/tomo-connector",
        server_url="http://tomo:8080",
        verify=False,
    )
    assert "releases/latest/download/tomo-connector-darwin-arm64" in script
    assert "SHA256SUMS" not in script


def test_normalize_os_arch() -> None:
    assert mod._normalize_os_arch("Linux", "x86_64") == "linux amd64"
    assert mod._normalize_os_arch("Darwin", "arm64") == "darwin arm64"
    assert mod._normalize_os_arch("linux", "aarch64") == "linux arm64"
    with pytest.raises(mod.InstallError):
        mod._normalize_os_arch("windows", "x86_64")


def test_systemd_unit_sane() -> None:
    unit = mod._systemd_unit()
    assert "ExecStart=%h/.local/bin/tomo-connector run" in unit
    assert "WantedBy=default.target" in unit


def test_ensure_pair_ok() -> None:
    mod._ensure_pair_ok(0, "paired ok", "")
    with pytest.raises(mod.InstallError) as ei:
        mod._ensure_pair_ok(1, "", "pairing failed")
    assert ei.value.stage == "pair"
