"""MCP server/item persistence: CRUD, cascade delete, and secret masking."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "mcp.db")


def test_mcp_secrets_are_ciphertext_and_public_views_are_masked(tmp_path: Path) -> None:
    _rebind(tmp_path)
    created = store.create_mcp_server(
        {
            "id": "github",
            "name": "GitHub",
            "transport": "streamable_http",
            "url": "https://mcp.example/mcp",
            "headers": {"Authorization": "Bearer secret-token"},
        }
    )
    assert created["headers_keys"] == ["Authorization"]
    assert created["headers_set"] is True
    assert "secret-token" not in str(created)
    raw = store._conn.execute(
        "SELECT headers_ciphertext FROM mcp_servers WHERE id='github'"
    ).fetchone()[0]
    assert raw.startswith("enc:v1:")
    assert "secret-token" not in raw


def test_create_requires_command_for_stdio(tmp_path: Path) -> None:
    _rebind(tmp_path)
    with pytest.raises(ValueError):
        store.create_mcp_server({"name": "x", "transport": "stdio"})


def test_create_requires_url_for_http(tmp_path: Path) -> None:
    _rebind(tmp_path)
    with pytest.raises(ValueError):
        store.create_mcp_server({"name": "x", "transport": "streamable_http"})


def test_create_stdio_server(tmp_path: Path) -> None:
    _rebind(tmp_path)
    created = store.create_mcp_server(
        {
            "id": "fs",
            "name": "Filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            "env": {"HOME": "/tmp"},
        }
    )
    assert created["command"] == "npx"
    assert created["args"] == ["-y", "@modelcontextprotocol/server-filesystem"]
    assert created["env_keys"] == ["HOME"]
    assert created["status"] == "unknown"


def test_update_blank_secret_preserves_ciphertext(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_mcp_server(
        {
            "id": "gh",
            "name": "GitHub",
            "transport": "streamable_http",
            "url": "https://mcp.example/mcp",
            "headers": {"Authorization": "Bearer secret-token"},
        }
    )
    before = store._conn.execute(
        "SELECT headers_ciphertext FROM mcp_servers WHERE id='gh'"
    ).fetchone()[0]
    # Omitting "headers" entirely leaves ciphertext untouched.
    updated = store.update_mcp_server("gh", {"name": "GitHub Renamed"})
    after = store._conn.execute(
        "SELECT headers_ciphertext FROM mcp_servers WHERE id='gh'"
    ).fetchone()[0]
    assert before == after
    assert updated["name"] == "GitHub Renamed"


def test_update_masked_placeholder_preserves_value(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_mcp_server(
        {
            "id": "gh2",
            "name": "GitHub",
            "transport": "streamable_http",
            "url": "https://mcp.example/mcp",
            "headers": {"Authorization": "Bearer secret-token", "X-Extra": "v1"},
        }
    )
    updated = store.update_mcp_server(
        "gh2", {"headers": {"Authorization": "••••", "X-Extra": "v2"}}
    )
    assert updated["headers_keys"] == ["Authorization", "X-Extra"]
    internal = store.get_mcp_server("gh2", include_secrets=True)
    assert internal["headers"]["Authorization"] == "Bearer secret-token"
    assert internal["headers"]["X-Extra"] == "v2"


def test_include_secrets_only_for_internal_use(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_mcp_server(
        {
            "id": "gh3",
            "name": "GitHub",
            "transport": "streamable_http",
            "url": "https://mcp.example/mcp",
            "headers": {"Authorization": "Bearer secret-token"},
        }
    )
    public = store.get_mcp_server("gh3")
    assert "headers" not in public
    assert "headers_ciphertext" not in public
    internal = store.get_mcp_server("gh3", include_secrets=True)
    assert internal["headers"] == {"Authorization": "Bearer secret-token"}


def test_delete_cascades_items(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_mcp_server(
        {"id": "srv", "name": "srv", "transport": "stdio", "command": "echo"}
    )
    store.replace_mcp_items(
        "srv",
        [
            {"kind": "tool", "runtime_id": "mcp__srv__echo", "name": "echo", "description": "d"},
        ],
    )
    assert len(store.list_mcp_items("srv")) == 1
    assert store.delete_mcp_server("srv") is True
    assert store._conn.execute(
        "SELECT COUNT(*) FROM mcp_items WHERE server_id='srv'"
    ).fetchone()[0] == 0
    assert store.delete_mcp_server("srv") is False


def test_replace_items_preserves_enabled_toggle(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_mcp_server(
        {"id": "srv2", "name": "srv2", "transport": "stdio", "command": "echo"}
    )
    items = store.replace_mcp_items(
        "srv2",
        [{"kind": "tool", "runtime_id": "mcp__srv2__echo", "name": "echo", "description": "d"}],
    )
    item_id = items[0]["id"]
    store.set_mcp_item_enabled(item_id, False)

    # Re-discovery (same kind/name/uri) must preserve the disabled state.
    refreshed = store.replace_mcp_items(
        "srv2",
        [{"kind": "tool", "runtime_id": "mcp__srv2__echo", "name": "echo", "description": "d2"}],
    )
    assert refreshed[0]["enabled"] is False
    assert refreshed[0]["description"] == "d2"


def test_set_mcp_status_updates_row(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_mcp_server(
        {"id": "srv3", "name": "srv3", "transport": "stdio", "command": "echo"}
    )
    updated = store.set_mcp_status("srv3", "connected", "ok", connected_at=123.0)
    assert updated["status"] == "connected"
    assert updated["status_message"] == "ok"
    assert updated["last_connected_at"] == 123.0


def test_reset_runtime_statuses(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_mcp_server(
        {"id": "srv4", "name": "srv4", "transport": "stdio", "command": "echo"}
    )
    store.set_mcp_status("srv4", "connected")
    store.reset_mcp_runtime_statuses()
    assert store.get_mcp_server("srv4")["status"] == "unknown"
