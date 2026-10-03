import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest
from starlette.middleware.sessions import SessionMiddleware

from app.plugins.manager import PluginManager
from app.runtime.tools.registry import ToolRegistry


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from app.plugins import manager as module

    value = PluginManager(tmp_path / "home")
    monkeypatch.setattr(module, "_manager", value)
    yield value
    value.close()


def source(tmp_path, text=None):
    path = tmp_path / "source"
    path.mkdir(exist_ok=True)
    (path / "tomo-plugin.json").write_text(
        json.dumps({"id": "test", "name": "Test", "version": "1", "sdk_version": 1})
    )
    (path / "plugin.py").write_text(
        text
        or """
def setup(api):
    api.page("/", "Overview")
    api.page("/details", "Details")
    @api.router.get("/")
    def home():
        return {"version": "one"}
    @api.router.get("/details")
    def details():
        return {"details": True}
    api.tool("value", "Value", {"type": "object"}, lambda args: {"version": "one"})
"""
    )
    return path


def client_for(manager, authenticated=True):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.middleware("http")
    async def auth(request: Request, call_next):
        if authenticated:
            request.state.auth_user_id = "alice"
        return await call_next(request)

    app.mount("/plugins", manager)
    return TestClient(app, follow_redirects=False)


def test_live_install_reload_disable_and_persist(manager, tmp_path):
    path = source(tmp_path)
    client = client_for(manager)
    registry = ToolRegistry()
    assert client.get("/plugins/test/").status_code == 404
    manager.install(str(path))
    assert "plugin__test__value" not in registry.names()
    manager.change("test", "enable")
    assert client.get("/plugins/test/").json() == {"version": "one"}
    assert client.get("/plugins/test/details").json() == {"details": True}
    assert "plugin__test__value" in registry.names()
    assert json.loads(registry.execute("plugin__test__value", {})) == {"version": "one"}
    (path / "plugin.py").write_text(
        (path / "plugin.py").read_text().replace('"one"', '"two"')
    )
    manager.change("test", "reload")
    assert client.get("/plugins/test/").json() == {"version": "two"}
    assert json.loads(registry.execute("plugin__test__value", {})) == {"version": "two"}
    restored = PluginManager(manager.root)
    assert not restored.list()[0]["running"]
    restored.start()
    assert restored.list()[0]["running"]
    restored.close()
    manager.change("test", "disable")
    assert client.get("/plugins/test/details").status_code == 404
    assert "plugin__test__value" not in registry.names()
    assert registry.execute("plugin__test__value", {}).startswith("Error:")
    assert not PluginManager(manager.root).list()[0]["running"]


def test_failed_reload_retains_old_plugin(manager, tmp_path):
    path = source(tmp_path)
    manager.install(str(path))
    manager.change("test", "enable")
    (path / "plugin.py").write_text("def setup(api):\n    raise ValueError('broken')\n")
    with pytest.raises(ValueError, match="broken"):
        manager.change("test", "reload")
    assert client_for(manager).get("/plugins/test/").json() == {"version": "one"}
    assert manager.list()[0]["running"]


def test_busy_plugin_cannot_change(manager, tmp_path):
    manager.install(str(source(tmp_path)))
    manager.change("test", "enable")
    with manager.lease("test"):
        for action in ("reload", "disable", "uninstall"):
            with pytest.raises(RuntimeError, match="in use"):
                manager.change("test", action)
    manager.change("test", "disable")


def test_plugin_pages_require_authentication(manager, tmp_path):
    manager.install(str(source(tmp_path)))
    manager.change("test", "enable")
    response = client_for(manager, authenticated=False).get("/plugins/test/details")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_setup_cleanup_and_uninstall_keep_data(manager, tmp_path):
    path = source(
        tmp_path,
        """
def setup(api):
    api.data_dir.mkdir(parents=True, exist_ok=True)
    api.on_dispose(lambda: (api.data_dir / "disposed").write_text("yes"))
""",
    )
    manager.install(str(path))
    manager.change("test", "enable")
    manager.change("test", "uninstall")
    assert manager.list() == []
    assert path.exists()
    assert (manager.root / "plugins/data/test/disposed").read_text() == "yes"


def test_manifest_validation_and_no_code_on_install(manager, tmp_path):
    path = source(tmp_path, "raise RuntimeError('must not run on install')")
    manager.install(str(path))
    with pytest.raises(RuntimeError):
        manager.change("test", "enable")
    assert not manager.list()[0]["running"]
    (path / "tomo-plugin.json").write_text(
        '{"id":"../escape","name":"Bad","sdk_version":1}'
    )
    with pytest.raises(ValueError):
        manager.manifest(path)


def test_external_tool_permission_assessment(tmp_path):
    from app.runtime.permissions.assess import assess

    for name, arguments in [
        ("plugin__money__summary", {}),
        ("plugin_manager", {"action": "enable"}),
    ]:
        assert any(
            f.kind == "external" for f in assess(name, arguments, tmp_path).findings
        )
    assert not assess("plugin_manager", {"action": "list"}, tmp_path).findings


def test_management_api_admin_and_csrf(manager, monkeypatch, tmp_path):
    from app.plugins.routes import router
    from app.services import store

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.middleware("http")
    async def auth(request, call_next):
        request.state.auth_user_id = "alice"
        return await call_next(request)

    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"role": "user", "enabled": True}
    )
    assert (
        client.post(
            "/api/plugins/install", json={"path": str(source(tmp_path))}
        ).status_code
        == 403
    )
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"role": "admin", "enabled": True}
    )
    assert (
        client.post(
            "/api/plugins/install",
            json={"path": str(tmp_path / "source")},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/plugins/install", json={"path": str(tmp_path / "source")}
        ).status_code
        == 200
    )
    from app.runtime import plugin_ideas

    async def ideas(user_id, refresh=False):
        return {"prompts": [], "source": "test", "user_id": user_id, "refresh": refresh}

    monkeypatch.setattr(plugin_ideas, "get_plugin_ideas", ideas)
    assert client.get("/api/plugins/ideas?refresh=true").json()["user_id"] == "alice"
    assert client.get("/api/plugins/ideas?refresh=true").json()["refresh"] is True
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"role": "user", "enabled": True}
    )
    assert client.get("/api/plugins/ideas").status_code == 403
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"role": "admin", "enabled": True}
    )
    assert client.post("/api/plugins/test/enable").status_code == 200
    assert client.get("/api/plugins").json()[0]["running"]
    with manager.lease("test"):
        assert client.post("/api/plugins/test/reload").status_code == 409
    assert client.post("/api/plugins/missing/enable").status_code == 404


def test_helper_imports_reload_without_stale_bytecode(manager, tmp_path):
    path = source(
        tmp_path,
        """
from .helper import value

def setup(api):
    api.tool("value", "Value", {"type": "object"}, lambda args: value)
""",
    )
    (path / "helper.py").write_text('value = "one"\n')
    manager.install(str(path))
    manager.change("test", "enable")
    assert manager.execute("plugin__test__value", {}) == "one"
    (path / "helper.py").write_text('value = "two"\n')
    manager.change("test", "reload")
    assert manager.execute("plugin__test__value", {}) == "two"


def test_failed_setup_disposes_resources(manager, tmp_path):
    path = source(
        tmp_path,
        """
def setup(api):
    api.data_dir.mkdir(parents=True, exist_ok=True)
    api.on_dispose(lambda: (api.data_dir / "disposed").write_text("yes"))
    raise ValueError("broken")
""",
    )
    manager.install(str(path))
    with pytest.raises(ValueError):
        manager.change("test", "enable")
    assert (manager.root / "plugins/data/test/disposed").read_text() == "yes"


def test_agent_management_tool_requires_admin(manager, monkeypatch, tmp_path):
    from app.runtime.tools.plugin_manager import run
    from app.services import store

    monkeypatch.setattr(
        store, "get_user", lambda uid: {"role": "user", "enabled": True}
    )
    assert run({"action": "install", "path": str(source(tmp_path))}).startswith(
        "Error:"
    )
    assert manager.list() == []
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"role": "admin", "enabled": True}
    )
    assert (
        json.loads(run({"action": "install", "path": str(tmp_path / "source")}))["id"]
        == "test"
    )
    assert json.loads(run({"action": "enable", "id": "test"}))["running"]


def test_startup_failure_isolated_from_other_plugins(manager, tmp_path):
    path = source(tmp_path)
    manager.install(str(path))
    manager.change("test", "enable")
    (path / "plugin.py").write_text(
        "def setup(api):\n    raise ValueError('broken startup')\n"
    )
    restored = PluginManager(manager.root)
    restored.start()
    assert not restored.list()[0]["running"]
    assert "broken startup" in restored.list()[0]["error"]
    assert manager.list()[0]["running"]
    restored.close()


def test_live_registry_respects_agent_tool_selection(manager, tmp_path):
    manager.install(str(source(tmp_path)))
    manager.change("test", "enable")
    registry = ToolRegistry()
    assert registry.get_openai_tools(enabled=["bash"])[0]["function"]["name"] == "bash"
    schemas = registry.get_openai_tools(enabled=["plugin__test__value"])
    assert [schema["function"]["name"] for schema in schemas] == ["plugin__test__value"]
    assert registry.get_definition("plugin__test__value")["backend"] == "plugin:test"
    manager.change("test", "disable")
    assert registry.get_openai_tools(enabled=["plugin__test__value"]) == []
