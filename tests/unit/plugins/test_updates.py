import io
import json
from pathlib import Path
import zipfile

import httpx
import pytest

from app.plugins import catalogs
from app.plugins.manager import PluginManager


def not_found(url):
    response = httpx.Response(404, request=httpx.Request("GET", url))
    response.raise_for_status()


@pytest.fixture
def remote(tmp_path, monkeypatch):
    state = dict(
        commit="a" * 40,
        value="one",
        version="1",
        id="demo",
        sdk=1,
        archives=0,
        manifests=0,
        fail=False,
        on_download=None,
    )
    manager = PluginManager(tmp_path / "home")
    from app.plugins import manager as module

    monkeypatch.setattr(module, "_manager", manager)

    def fetch(url, limit=0):
        if "/commits/" in url:
            return json.dumps({"sha": state["commit"]}).encode()
        if "/git/ref/heads/" in url:
            if not url.endswith("/main"):
                return not_found(url)
            return json.dumps(
                {"ref": "refs/heads/main", "object": {"sha": state["commit"]}}
            ).encode()
        if "/git/ref/tags/" in url:
            return json.dumps({"ref": "refs/tags/v1"}).encode()
        manifest = dict(
            id=state["id"],
            name="Demo",
            version=state["version"],
            sdk_version=state["sdk"],
        )
        if url.endswith("/tomo-plugin.json"):
            state["manifests"] += 1
            return json.dumps(manifest).encode()
        assert "codeload.github.com" in url
        state["archives"] += 1
        # Only the candidate checked above may be downloaded.
        assert url.endswith("/" + state["commit"])
        if state["on_download"]:
            state["on_download"]()
        code = (
            'raise ValueError("broken update")'
            if state["fail"]
            else f'''
def setup(api):
    api.tool("value", "Value", {{"type": "object"}}, lambda args: "{state["value"]}")
    api.page("/", "Overview")
    @api.router.get("/")
    def overview():
        return {{"value": "{state["value"]}"}}
'''
        )
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as package:
            package.writestr("repo/plugins/demo/tomo-plugin.json", json.dumps(manifest))
            package.writestr("repo/plugins/demo/plugin.py", code)
            package.writestr(
                "repo/plugins/demo/skills/guide/SKILL.md",
                f"---\nname: guide\n---\n{state['value']} instructions",
            )
        return stream.getvalue()

    monkeypatch.setattr(catalogs, "fetch_bytes", fetch)
    # updates imports fetch_bytes once; patch both bindings.
    from app.plugins import updates

    monkeypatch.setattr(updates, "fetch_bytes", fetch)
    installed = manager.install("https://github.com/owner/repo.git", "plugins/demo")
    yield manager, state, installed
    manager.close()


def advance(state):
    state.update(commit="b" * 40, value="two")


def test_same_version_commit_update_is_live_and_preserves_data(remote):
    from app.extensions.skills import read_skill_body
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    manager, state, installed = remote
    manager.change("demo", "enable")
    from starlette.middleware.sessions import SessionMiddleware

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.middleware("http")
    async def auth(request, call_next):
        request.state.auth_user_id = "alice"
        return await call_next(request)

    app.mount("/plugins", manager)
    client = TestClient(app)
    assert client.get("/plugins/demo/").json() == {"value": "one"}
    data = manager.root / "plugins/data/demo/saved.txt"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("keep me")
    advance(state)
    assert manager.check_updates()[0]["status"] == "available"
    assert state["archives"] == 1  # Checking downloads metadata only.
    assert manager.list()[0]["update"]["latest_version"] == installed["version"]
    result = manager.change("demo", "update")
    assert result["updated"] and result["running"] and result["enabled"]
    assert client.get("/plugins/demo/").json() == {"value": "two"}
    assert manager.execute("plugin__demo__value", {}) == "two"
    assert read_skill_body("plugin__demo__guide") == "two instructions"
    assert data.read_text() == "keep me"
    row = manager.list()[0]
    assert row["version"] == installed["version"] and row["commit"] == "b" * 40
    assert row["path"] != installed["path"]
    assert row["update"]["status"] == "current"
    restored = PluginManager(manager.root)
    restored.start()
    assert restored.execute("plugin__demo__value", {}) == "two"
    restored.close()


def test_disabled_update_never_executes_and_stays_disabled(remote):
    manager, state, _ = remote
    advance(state)
    state["fail"] = True
    result = manager.change("demo", "update")
    assert result["updated"] and not result["enabled"] and not result["running"]
    assert not manager.definitions()
    assert manager.list()[0]["commit"] == "b" * 40


def test_failed_activation_preserves_previous_snapshot(remote):
    manager, state, installed = remote
    manager.change("demo", "enable")
    advance(state)
    state["fail"] = True
    manager.check_updates()
    with pytest.raises(ValueError, match="broken update"):
        manager.change("demo", "update")
    row = manager.list()[0]
    assert row["path"] == installed["path"] and row["commit"] == "a" * 40
    assert row["enabled"] and row["running"] and "Update failed" in row["error"]
    assert manager.execute("plugin__demo__value", {}) == "one"
    assert row["update"]["status"] == "available"
    assert len(list((manager.root / "plugins/sources").iterdir())) == 1


def test_persistence_failure_does_not_swap_runtime(remote, monkeypatch):
    manager, state, installed = remote
    manager.change("demo", "enable")
    advance(state)

    def fail_save():
        raise OSError("disk full")

    monkeypatch.setattr(manager, "_save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        manager.change("demo", "update")
    assert manager.list()[0]["path"] == installed["path"]
    assert PluginManager(manager.root).list()[0]["commit"] == "a" * 40
    assert manager.execute("plugin__demo__value", {}) == "one"
    assert len(list((manager.root / "plugins/sources").iterdir())) == 1


@pytest.mark.parametrize("field,value", [("id", "other"), ("sdk", 2)])
def test_incompatible_candidate_not_downloaded(remote, field, value):
    manager, state, _ = remote
    advance(state)
    state[field] = value
    assert manager.check_updates()[0]["status"] == "error"
    assert state["archives"] == 1
    with pytest.raises(ValueError):
        manager.change("demo", "update")
    assert manager.list()[0]["commit"] == "a" * 40


def test_check_cache_force_and_noop_update(remote):
    manager, state, _ = remote
    assert manager.check_updates()[0]["status"] == "current"
    advance(state)
    assert manager.check_updates(force=False)[0]["status"] == "current"
    assert manager.check_updates(force=True)[0]["status"] == "available"
    manager.change("demo", "update")
    assert not manager.change("demo", "update")["updated"]
    assert state["archives"] == 2


def test_busy_plugin_before_and_during_download(remote):
    manager, state, _ = remote
    manager.change("demo", "enable")
    advance(state)
    with manager.lease("demo"):
        with pytest.raises(RuntimeError, match="in use"):
            manager.change("demo", "update")
    assert state["archives"] == 1
    lease = manager.lease("demo")
    state["on_download"] = lambda: lease.__enter__()
    try:
        with pytest.raises(RuntimeError, match="in use"):
            manager.change("demo", "update")
    finally:
        lease.__exit__(None, None, None)
    assert manager.execute("plugin__demo__value", {}) == "one"
    assert len(list((manager.root / "plugins/sources").iterdir())) == 1


def test_concurrent_lifecycle_change_rejects_candidate(remote):
    manager, state, _ = remote
    manager.change("demo", "enable")
    advance(state)
    state["on_download"] = lambda: manager.change("demo", "reload")
    with pytest.raises(RuntimeError, match="changed during update"):
        manager.change("demo", "update")
    assert manager.execute("plugin__demo__value", {}) == "one"
    assert manager.list()[0]["commit"] == "a" * 40


@pytest.mark.parametrize("ref", ["v1", "a" * 40, "refs/tags/v1", "abcdef1"])
def test_tags_and_commits_stay_pinned(remote, ref):
    manager, state, _ = remote
    manager._rows["demo"]["origin"]["ref"] = ref
    advance(state)
    assert manager.check_updates()[0]["status"] == "pinned"
    with pytest.raises(ValueError, match="pinned"):
        manager.change("demo", "update")
    assert state["archives"] == 1


def test_local_source_uses_reload(remote):
    manager, _, installed = remote
    manager._rows["demo"]["source"] = "path"
    assert manager.check_updates()[0]["status"] == "local"
    with pytest.raises(ValueError, match="local source"):
        manager.change("demo", "update")
    assert Path(installed["path"]).exists()


def test_network_error_is_not_reported_as_up_to_date(remote, monkeypatch):
    from app.plugins import updates

    manager, _, _ = remote

    def offline(url, limit=0):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(updates, "fetch_bytes", offline)
    assert manager.check_updates()[0]["status"] == "error"
    assert manager.list()[0]["update"]["status"] == "error"
    assert manager.list()[0]["commit"] == "a" * 40


def test_branch_moving_during_update_does_not_change_downloaded_revision(
    remote, monkeypatch
):
    from app.plugins import updates

    manager, state, _ = remote
    manager.change("demo", "enable")
    advance(state)
    original = catalogs.fetch_bytes
    archive = original("https://codeload.github.com/owner/repo/zip/" + state["commit"])

    def moving_fetch(url, limit=0):
        if "codeload.github.com" in url:
            assert url.endswith("/" + "b" * 40)
            return archive
        content = original(url, limit)
        if url.endswith("/tomo-plugin.json"):
            state.update(commit="c" * 40, value="three")
        return content

    monkeypatch.setattr(catalogs, "fetch_bytes", moving_fetch)
    monkeypatch.setattr(updates, "fetch_bytes", moving_fetch)
    result = manager.change("demo", "update")
    assert result["commit"] == "b" * 40
    assert manager.execute("plugin__demo__value", {}) == "two"


def test_updates_keep_working_without_original_marketplace(remote):
    manager, state, _ = remote
    manager._rows["demo"]["marketplace"] = "removed-catalog"
    advance(state)
    assert manager.check_updates()[0]["status"] == "available"
    assert manager.change("demo", "update")["updated"]


def test_api_and_agent_update_controls_require_admin(remote, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware
    from app.plugins.routes import router
    from app.runtime.tools.plugin_manager import run
    from app.services import store

    manager, state, _ = remote
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.middleware("http")
    async def auth(request, call_next):
        request.state.auth_user_id = "alice"
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)
    role = {"role": "user", "enabled": True}
    monkeypatch.setattr(store, "get_user", lambda uid: role)
    assert client.post("/api/plugins/check-updates", json={}).status_code == 403
    assert client.post("/api/plugins/demo/update").status_code == 403
    assert run({"action": "outdated"}).startswith("Error:")
    role["role"] = "admin"
    assert (
        client.post(
            "/api/plugins/check-updates",
            json={},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    advance(state)
    assert (
        client.post("/api/plugins/check-updates", json={"force": False}).json()[0][
            "status"
        ]
        == "available"
    )
    assert json.loads(run({"action": "outdated"}))[0]["status"] == "available"
    assert json.loads(run({"action": "update", "id": "demo"}))["updated"]
    assert client.post("/api/plugins/demo/update").json()["updated"] is False
    assert manager.list()[0]["commit"] == "b" * 40


def test_repository_version_is_reported_and_saved_after_update(remote):
    manager, state, _ = remote
    manager.change("demo", "enable")
    advance(state)
    state["version"] = "1.1.0"
    candidate = manager.check_updates()[0]
    assert candidate["installed_version"] == "1"
    assert candidate["latest_version"] == "1.1.0"
    assert manager.list()[0]["version"] == "1"
    result = manager.change("demo", "update")
    assert result["version"] == "1.1.0"
    assert manager.list()[0]["version"] == "1.1.0"
    assert manager.check_updates()[0]["latest_version"] == "1.1.0"
