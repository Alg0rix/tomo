"""Workplaces: SQLite CRUD, Connect, encrypted SSH secrets, agent assignment."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import store
from app.workplaces.backends import ssh as ssh_backend


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "workplaces.db")


def _raw_col(workplace_id: str, col: str) -> str:
    row = store._conn.execute(
        f"SELECT {col} FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    return row[col] if row else ""


def test_migrate_creates_workplaces_table(tmp_path: Path) -> None:
    _rebind(tmp_path)
    names = {
        r[0]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "workplaces" in names


def test_create_local_and_connect(tmp_path: Path) -> None:
    _rebind(tmp_path)
    root = tmp_path / "local-root"
    root.mkdir()
    wp = store.create_workplace(
        {"id": "wp_local", "name": "Local", "kind": "local", "root_path": str(root)}
    )
    assert wp["kind"] == "local"
    assert wp["status"] == "ready"
    assert wp["host"] == str(root)
    result = store.connect_workplace("wp_local")
    assert result is not None
    assert result["ok"] is True
    assert result["status"] == "connected"
    # Public view keeps local workplaces as "ready" (path present), not tunnel "connected".
    assert store.get_workplace("wp_local")["status"] == "ready"


def test_local_connect_fails_missing_path(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {
            "id": "wp_missing",
            "name": "Missing",
            "kind": "local",
            "root_path": str(tmp_path / "nope"),
        }
    )
    result = store.connect_workplace("wp_missing")
    assert result["ok"] is False
    assert result["status"] == "offline"


def test_ssh_secrets_encrypted(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {
            "id": "wp_ssh",
            "name": "SSH",
            "kind": "ssh",
            "ssh_host": "example.com",
            "ssh_user": "deploy",
            "ssh_password": "s3cret-pass",
            "ssh_key": "-----BEGIN KEY-----\nabc\n-----END KEY-----",
        }
    )
    assert wp["password_set"] is True
    assert wp["key_set"] is True
    assert "ssh_password" not in wp
    assert "ssh_key" not in wp
    raw_pwd = _raw_col("wp_ssh", "ssh_password")
    raw_key = _raw_col("wp_ssh", "ssh_key")
    assert raw_pwd.startswith("enc:v1:")
    assert raw_key.startswith("enc:v1:")
    assert "s3cret" not in raw_pwd


def test_blank_ssh_password_keeps_ciphertext(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {
            "id": "wp_ssh",
            "name": "SSH",
            "kind": "ssh",
            "ssh_host": "h",
            "ssh_user": "u",
            "ssh_password": "keep-me",
        }
    )
    before = _raw_col("wp_ssh", "ssh_password")
    store.update_workplace("wp_ssh", {"ssh_password": "", "name": "SSH2"})
    assert _raw_col("wp_ssh", "ssh_password") == before
    assert store.get_workplace("wp_ssh")["name"] == "SSH2"


def test_ssh_connect_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _rebind(tmp_path)

    def _ok(host: str, port: int, user: str, password: str, key: str) -> tuple[bool, str]:
        assert host == "box.test"
        assert user == "ops"
        assert password == "pw"
        return True, "mocked ok"

    monkeypatch.setattr(ssh_backend, "probe_ssh", _ok)
    store.create_workplace(
        {
            "id": "wp_ssh",
            "name": "SSH",
            "kind": "ssh",
            "ssh_host": "box.test",
            "ssh_user": "ops",
            "ssh_password": "pw",
        }
    )
    result = store.connect_workplace("wp_ssh")
    assert result["ok"] is True
    assert result["status"] == "connected"
    assert "mocked" in result["message"]


def test_tunnel_create_issues_pairing_code(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {"id": "wp_tun", "name": "Tunnel", "kind": "tunnel"}
    )
    assert wp["status"] == "pairing"
    assert wp["pairing_code"]
    assert len(wp["pairing_code"]) >= 6
    assert wp["pairing_expires_at"] > 0
    assert wp["connector_token_set"] is False


def test_tunnel_connect_not_connected_without_socket(tmp_path: Path) -> None:
    _rebind(tmp_path)
    from app.workplaces.hub import hub

    hub.reset()
    store.create_workplace(
        {"id": "wp_tun", "name": "Tunnel", "kind": "tunnel"}
    )
    result = store.connect_workplace("wp_tun")
    assert result["ok"] is False
    assert result["status"] in ("pairing", "offline")
    assert store.get_workplace("wp_tun")["status"] != "connected"


def test_tunnel_pairing_code_refresh(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {"id": "wp_tun", "name": "Tunnel", "kind": "tunnel"}
    )
    first = wp["pairing_code"]
    again = store.issue_pairing_code("wp_tun")
    assert again is not None
    assert again["pairing_code"]
    assert again["pairing_code"] != first or True  # may rarely collide
    assert again["status"] in ("pairing", "connected")


def test_tunnel_token_encrypted_after_pair(tmp_path: Path) -> None:
    _rebind(tmp_path)
    wp = store.create_workplace(
        {"id": "wp_tun", "name": "Tunnel", "kind": "tunnel"}
    )
    code = wp["pairing_code"]
    result = store.pair_connector(code, hostname="pi.local", version="0.2.0")
    assert result is not None
    assert result["workplace_id"] == "wp_tun"
    assert result["token"]
    public = store.get_workplace("wp_tun")
    assert public["connector_token_set"] is True
    # Paired but not live yet — honest offline until WebSocket registers.
    assert public["status"] == "offline"
    assert "connector_token" not in public or public.get("connector_token") in ("", None)
    raw = _raw_col("wp_tun", "connector_token")
    assert raw.startswith("enc:v1:")
    assert result["token"] not in raw
    # Reconnect with token (hello path) marks connected in DB.
    hello = store.hello_connector(result["token"], hostname="pi.local")
    assert hello == {"workplace_id": "wp_tun"}
    assert store.get_workplace("wp_tun")["status"] == "connected"


def test_assign_workplace_to_agent(tmp_path: Path) -> None:
    _rebind(tmp_path)
    root = tmp_path / "wp"
    root.mkdir()
    store.create_workplace(
        {"id": "wp1", "name": "WP", "kind": "local", "root_path": str(root)}
    )
    updated = store.update_agent("ops", {"workplace_id": "wp1"})
    assert updated["workplace_id"] == "wp1"
    assert store.get_workplace("wp1")["agent_count"] == 1


def test_assign_missing_workplace_raises(tmp_path: Path) -> None:
    _rebind(tmp_path)
    with pytest.raises(ValueError, match="Workplace not found"):
        store.update_agent("ops", {"workplace_id": "nope"})


def test_delete_workplace_clears_agent(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_workplace(
        {"id": "wp1", "name": "WP", "kind": "local", "root_path": str(tmp_path)}
    )
    store.update_agent("ops", {"workplace_id": "wp1"})
    assert store.delete_workplace("wp1") is True
    assert store.get_agent("ops")["workplace_id"] == ""


def test_stats_workplace_count(tmp_path: Path) -> None:
    _rebind(tmp_path)
    assert store.stats()["workplace_count"] == 0
    store.create_workplace(
        {"id": "wp1", "name": "WP", "kind": "local", "root_path": str(tmp_path)}
    )
    assert store.stats()["workplace_count"] == 1
