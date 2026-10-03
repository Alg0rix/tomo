import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

from app.plugins import catalogs
from app.plugins.catalogs import Catalog, GitSource, Marketplaces
from app.plugins.manager import PluginManager


def entry(plugin_id="money", version="0.1.0"):
    return {
        "id": plugin_id,
        "name": "Money",
        "description": "Ledger",
        "version": version,
        "sdk_version": 1,
        "author": "author",
        "source": {
            "type": "git",
            "url": "https://github.com/Alg0rix/tomo-plugins.git",
            "ref": "main",
            "subdirectory": "plugins/money",
        },
    }


def manifest(identity="custom", plugins=None, name="Custom"):
    return {
        "schema_version": 1,
        "id": identity,
        "name": name,
        "plugins": [entry()] if plugins is None else plugins,
    }


@pytest.fixture
def markets(tmp_path, monkeypatch):
    value = Marketplaces(tmp_path / "home")
    monkeypatch.setattr(catalogs, "_marketplaces", value)
    return value


def add_custom(markets, tmp_path, plugins=None):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(manifest(plugins=plugins)))
    markets.add("path:" + str(path))
    return path


def test_defaults_independent_and_discovery_does_not_install(markets, monkeypatch):
    def fetch(url, limit=0):
        if url == catalogs.OFFICIAL_URL:
            return json.dumps(manifest("tomo-official", name="Tomo Official")).encode()
        return json.dumps(
            manifest("tomo-community", plugins=[], name="Tomo Community")
        ).encode()

    monkeypatch.setattr(catalogs, "fetch_bytes", fetch)
    markets.refresh("tomo-official")
    markets.refresh("tomo-community")
    assert markets.resolve("money@tomo-official")["marketplace"] == "tomo-official"
    rows = {row["id"]: row for row in markets.list()}
    assert rows["tomo-official"]["plugin_count"] == 1
    assert rows["tomo-community"]["plugin_count"] == 0
    assert not (markets.root / "plugins/registry.json").exists()
    assert not (markets.root / "plugins/sources").exists()
    with pytest.raises(ValueError, match="cannot be removed"):
        markets.remove("tomo-official")


def test_add_refresh_failure_preserves_last_valid_catalog(markets, tmp_path):
    path = add_custom(markets, tmp_path)
    assert len(markets.search("ledger")) == 1
    path.write_text("bad json")
    with pytest.raises(ValueError):
        markets.refresh("custom")
    assert markets.resolve("money@custom")["version"] == "0.1.0"
    assert Marketplaces(markets.root).resolve("money@custom")["version"] == "0.1.0"
    path.write_text(json.dumps(manifest("changed")))
    with pytest.raises(ValueError, match="identity changed"):
        markets.refresh("custom")
    assert len(markets.search()) == 1


@pytest.mark.parametrize(
    "source",
    [
        {"type": "git", "url": "http://github.com/owner/repo"},
        {"type": "git", "url": "https://evil.example/repo"},
        {
            "type": "git",
            "url": "https://github.com/owner/repo",
            "subdirectory": "../escape",
        },
        {"type": "git", "url": "https://github.com/owner/repo", "ref": "--evil"},
        {
            "type": "git",
            "url": "https://github.com/owner/repo",
            "install_script": "evil",
        },
    ],
)
def test_source_validation(source):
    with pytest.raises(ValueError):
        GitSource.model_validate(source)


def test_duplicate_ids_and_reserved_names_rejected(markets, tmp_path):
    with pytest.raises(ValueError):
        Catalog.model_validate(manifest(plugins=[entry(), entry()]))
    path = tmp_path / "spoof.json"
    path.write_text(json.dumps(manifest(name="Tomo Official")))
    with pytest.raises(ValueError, match="reserved"):
        markets.add("path:" + str(path))


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for path, content in files.items():
            package.writestr(path, content)
    return buffer.getvalue()


def mock_repository(monkeypatch, payload):
    def fetch(url, limit=0):
        if "/commits/" in url:
            return json.dumps({"sha": "a" * 40}).encode()
        return payload

    monkeypatch.setattr(catalogs, "fetch_bytes", fetch)


def plugin_archive(version="0.1.0"):
    return archive(
        {
            "repo/plugins/money/tomo-plugin.json": json.dumps(
                {"id": "money", "name": "Money", "version": version, "sdk_version": 1}
            ),
            "repo/plugins/money/plugin.py": "raise RuntimeError('installation must not execute this')",
        }
    )


def test_catalog_install_disabled_and_removal_keeps_installed_plugin(
    markets, tmp_path, monkeypatch
):
    add_custom(markets, tmp_path)
    mock_repository(monkeypatch, plugin_archive())
    manager = PluginManager(markets.root)
    row = manager.install("money@custom")
    assert row["commit"] == "a" * 40
    assert row["marketplace"] == "custom"
    assert not row["running"] and not row["enabled"]
    markets.remove("custom")
    assert (
        manager.list()[0]["origin"]["url"]
        == "https://github.com/Alg0rix/tomo-plugins.git"
    )
    assert Path(row["path"]).exists()
    with pytest.raises(RuntimeError, match="must not execute"):
        manager.change("money", "enable")


def test_direct_repo_install_bypasses_marketplaces(markets, tmp_path, monkeypatch):
    mock_repository(monkeypatch, plugin_archive())
    manager = PluginManager(markets.root)
    row = manager.install(
        "https://github.com/Alg0rix/tomo-plugins.git", "plugins/money"
    )
    assert row["id"] == "money" and "marketplace" not in row
    assert markets.search() == []


def test_catalog_source_mismatch_discards_download(markets, tmp_path, monkeypatch):
    add_custom(markets, tmp_path)
    mock_repository(monkeypatch, plugin_archive(version="0.2.0"))
    manager = PluginManager(markets.root)
    with pytest.raises(ValueError, match="differs from the catalog"):
        manager.install("money@custom")
    assert manager.list() == []
    assert list((markets.root / "plugins/sources").iterdir()) == []


@pytest.mark.parametrize("path", ["repo/../../escape", "/absolute", "repo/back\\slash"])
def test_archive_traversal_rejected(tmp_path, monkeypatch, path):
    mock_repository(monkeypatch, archive({path: "evil"}))
    with pytest.raises(ValueError, match="Unsafe archive"):
        catalogs.download_repository(
            GitSource(url="https://github.com/owner/repo"), tmp_path / "download"
        )
    assert not (tmp_path / "escape").exists()


def test_archive_symlink_rejected(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        info = zipfile.ZipInfo("repo/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        package.writestr(info, "../../escape")
    mock_repository(monkeypatch, buffer.getvalue())
    with pytest.raises(ValueError, match="symlinks"):
        catalogs.download_repository(
            GitSource(url="https://github.com/owner/repo"), tmp_path / "download"
        )


def test_marketplace_page_and_administration(markets, tmp_path, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware
    from app.plugins.routes import router
    from app.services import store

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.middleware("http")
    async def auth(request: Request, call_next):
        request.state.auth_user_id = "usr_admin"
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(manifest()))
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"role": "user", "enabled": True}
    )
    assert (
        client.post(
            "/api/marketplaces", json={"source": "path:" + str(path)}
        ).status_code
        == 403
    )
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"role": "admin", "enabled": True}
    )
    assert (
        client.post(
            "/api/marketplaces", json={"source": "path:" + str(path)}
        ).status_code
        == 200
    )
    assert (
        client.get("/api/plugins/catalog?q=money").json()[0]["marketplace"] == "custom"
    )
    assert client.delete("/api/marketplaces/custom").status_code == 200
