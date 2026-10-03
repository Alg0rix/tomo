import json
from pathlib import Path
import sqlite3

from fastapi import Request
from fastapi.testclient import TestClient
import pytest

from app.plugins.manager import PluginManager
from app.plugins.migration import migrate_module_catalog


def mock_official_download(monkeypatch, tmp_path):
    from app.plugins import catalogs

    def prepare(spec, root, subdirectory="", ref="main"):
        identity = Path(subdirectory).name
        path = tmp_path / "official" / identity
        path.mkdir(parents=True, exist_ok=True)
        (path / "tomo-plugin.json").write_text(
            json.dumps(
                {
                    "id": identity,
                    "name": identity,
                    "version": "1",
                    "sdk_version": 1,
                    "icon": "chart-column"
                    if identity == "token_monitor"
                    else "columns-3",
                }
            )
        )
        (path / "plugin.py").write_text(
            "def setup(api):\n    api.page('/', 'Overview')\n"
        )
        return (
            path,
            {
                "source": "git",
                "origin": {"url": spec, "subdirectory": subdirectory, "ref": ref},
                "commit": "test-sha",
            },
            None,
            None,
        )

    monkeypatch.setattr(catalogs, "prepare_install", prepare)


def test_one_time_migration_preserves_enablement_and_usage(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE modules (id TEXT PRIMARY KEY, enabled INTEGER);
        INSERT INTO modules VALUES ('token_monitor', 0), ('kanban', 1);
        CREATE TABLE usage_events (id INTEGER, prompt_tokens INTEGER);
        INSERT INTO usage_events VALUES (1, 123);
    """)
    home = tmp_path / "home"
    migrate_module_catalog(conn, home)
    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='modules'"
    ).fetchone()
    assert conn.execute("SELECT prompt_tokens FROM usage_events").fetchone()[0] == 123
    state_path = home / "plugins/registry.json"
    state = json.loads(state_path.read_text())
    assert not state["token_monitor"]["enabled"]
    assert state["kanban"]["enabled"]
    before = state_path.read_text()
    migrate_module_catalog(conn, home)
    assert state_path.read_text() == before
    mock_official_download(monkeypatch, tmp_path)
    manager = PluginManager(home)
    manager.start()
    try:
        rows = {row["id"]: row for row in manager.list()}
        assert not rows["token_monitor"]["running"]
        assert rows["kanban"]["running"]
    finally:
        manager.close()
        conn.close()


def test_migration_failure_keeps_old_catalog(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE modules (id TEXT, enabled INTEGER); INSERT INTO modules VALUES ('token_monitor',0);"
    )
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    (home / "plugins/registry.json").write_text("invalid json")
    with pytest.raises(ValueError):
        migrate_module_catalog(conn, home)
    assert conn.execute("SELECT enabled FROM modules").fetchone()[0] == 0
    conn.close()


def test_hub_renders_catalog_and_agent_builder(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.main import create_app
    from app.plugins import catalogs
    from app.services import store

    entry = dict(
        id="money",
        name="Money",
        description="Money <script>alert(1)</script>",
        author="Tomo",
        version="0.1.0",
        marketplace="tomo-official",
        publisher="Tomo Official",
        source=dict(
            url="https://github.com/Alg0rix/tomo-plugins",
            ref="main",
            subdirectory="plugins/money",
        ),
    )
    monkeypatch.setattr(
        catalogs,
        "_marketplaces",
        SimpleNamespace(
            search=lambda: [entry],
            list=lambda: [dict(id="tomo-official", name="Tomo Official")],
        ),
    )
    from app.plugins import manager as manager_module

    store.rebind(tmp_path / "hub.db")
    monkeypatch.setattr(
        manager_module, "_manager", PluginManager(tmp_path / "hub-home")
    )
    store.update_settings({"setup_complete": True})
    app = create_app()

    @app.middleware("http")
    async def auth(request: Request, call_next):
        request.state.auth_user_id = "usr_admin"
        return await call_next(request)

    client = TestClient(app)
    response = client.get("/extensions")
    assert response.status_code == 200
    assert 'data-catalog-install="money@tomo-official"' in response.text
    assert 'id="plugin-build"' in response.text
    assert 'id="hub-marketplaces"' in response.text
    assert "Money &lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert client.get("/extensions/guide").status_code == 200


def test_bundled_registry_moves_to_official_and_can_be_uninstalled(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    state = home / "plugins/registry.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "token_monitor": {
                    "id": "token_monitor",
                    "name": "Token Monitor",
                    "description": "Usage",
                    "version": "0.3",
                    "sdk_version": 1,
                    "source": "builtin",
                    "path": "/deleted/core/plugin",
                    "enabled": False,
                }
            }
        )
    )
    data = home / "plugins/data/token_monitor"
    data.mkdir(parents=True)
    (data / "saved.txt").write_text("keep")
    mock_official_download(monkeypatch, tmp_path)
    manager = PluginManager(home)
    manager.start()
    row = manager.list()[0]
    assert row["source"] == "git"
    assert row["marketplace"] == "tomo-official"
    assert row["icon"] == "chart-column"
    assert not row["enabled"] and not row["running"]
    assert row["commit"] == "test-sha"
    manager.change("token_monitor", "enable")
    assert manager.list()[0]["running"]
    manager.change("token_monitor", "uninstall")
    assert manager.list() == []
    assert (data / "saved.txt").read_text() == "keep"


def test_official_migration_failure_preserves_state_and_retries(tmp_path, monkeypatch):
    from app.plugins import catalogs

    home = tmp_path / "home"
    state = home / "plugins/registry.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "kanban": {
                    "id": "kanban",
                    "name": "Task Board",
                    "description": "Board",
                    "version": "0.3",
                    "sdk_version": 1,
                    "source": "builtin",
                    "path": "/old/core/plugin",
                    "enabled": True,
                }
            }
        )
    )
    before = state.read_text()
    monkeypatch.setattr(
        catalogs,
        "prepare_install",
        lambda *a: (_ for _ in ()).throw(OSError("offline")),
    )
    manager = PluginManager(home)
    manager.start()
    assert state.read_text() == before
    assert not manager.list()[0]["running"]
    assert "offline" in manager.list()[0]["error"]
    mock_official_download(monkeypatch, tmp_path)
    manager.change("kanban", "enable")
    assert manager.list()[0]["running"]
    assert not manager.list()[0]["error"]
    manager.close()


def test_fresh_install_has_no_feature_code_or_network_startup(tmp_path, monkeypatch):
    from app.plugins import catalogs

    monkeypatch.setattr(
        catalogs,
        "prepare_install",
        lambda *a: pytest.fail("Unexpected startup download"),
    )
    manager = PluginManager(tmp_path)
    manager.start()
    assert manager.list() == []
