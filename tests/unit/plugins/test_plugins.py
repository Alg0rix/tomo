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
    from app.services import store

    # Deterministic identity store: private plugin routes authenticate the
    # real seeded Admin instead of the legacy fake "alice" state.
    store.rebind(tmp_path / "plugins-identity.db")
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
            request.state.auth_user_id = "usr_admin"
        return await call_next(request)

    app.mount("/plugins", manager)
    return TestClient(app, follow_redirects=False)


def test_live_install_reload_disable_and_persist(manager, tmp_path):
    from tests.fakes.access import owned_admin_scope

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
    # Owned Admin execution: plugin tools admit the revalidated owned
    # ceiling while enabled (real enablement gate, real audit).
    with owned_admin_scope():
        assert json.loads(registry.execute("plugin__test__value", {})) == {"version": "one"}
    (path / "plugin.py").write_text(
        (path / "plugin.py").read_text().replace('"one"', '"two"')
    )
    manager.change("test", "reload")
    assert client.get("/plugins/test/").json() == {"version": "two"}
    with owned_admin_scope():
        assert json.loads(registry.execute("plugin__test__value", {})) == {"version": "two"}
    restored = PluginManager(manager.root)
    assert not restored.list()[0]["running"]
    restored.start()
    assert restored.list()[0]["running"]
    restored.close()
    manager.change("test", "disable")
    assert client.get("/plugins/test/details").status_code == 404
    assert "plugin__test__value" not in registry.names()
    # Disabled plugins fail closed even for an owned Admin ceiling.
    with owned_admin_scope():
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


PUBLIC_PLUGIN = '''
from fastapi import Request

def setup(api):
    @api.router.get("/")
    def dashboard():
        return {"private": True}

    @api.router.post("/responses")
    def private_submit():
        return {"private": True}

    @api.router.get("/publicity")
    def private_prefix():
        return {"private": True}

    @api.public_router.get("/")
    def landing(request: Request):
        return api.render_public(request, "survey.html")

    @api.public_router.post("/responses")
    def submit(answer: dict):
        return {"accepted": answer["answer"]}
'''


@pytest.fixture
def public_plugin(manager, tmp_path):
    path = source(tmp_path, PUBLIC_PLUGIN)
    (path / "templates").mkdir()
    (path / "templates/survey.html").write_text(
        '<h1>Survey</h1><form action="{{ plugin.base_url }}/responses"></form>'
        '<link rel="stylesheet" href="{{ plugin.static_url }}/style.css">'
        '{{ current_user_id|default("anonymous") }}'
    )
    (path / "static").mkdir()
    (path / "static/style.css").write_text("static")
    manager.install(str(path))
    return path


def test_public_routes_allow_anonymous_visitors(manager, public_plugin, monkeypatch):
    def no_account_context(*args, **kwargs):
        raise AssertionError("Public rendering must not load account context")

    monkeypatch.setattr("app.web.context.page_ctx", no_account_context)
    anonymous = client_for(manager, authenticated=False)
    assert anonymous.get("/plugins/test/public/").status_code == 404
    manager.change("test", "enable")
    landing = anonymous.get("/plugins/test/public/")
    assert landing.status_code == 200
    assert "<h1>Survey</h1>" in landing.text
    assert 'action="/plugins/test/public/responses"' in landing.text
    assert 'href="/plugins/test/static/style.css"' in landing.text
    assert "anonymous" in landing.text
    assert anonymous.post("/plugins/test/public/responses", json={"answer": "yes"}).json() == {
        "accepted": "yes"
    }


def test_private_routes_stay_authenticated(manager, public_plugin):
    manager.change("test", "enable")
    anonymous = client_for(manager, authenticated=False)
    for url in ("/plugins/test/", "/plugins/test/publicity"):
        assert anonymous.get(url).status_code == 303
    assert anonymous.post("/plugins/test/responses", json={"answer": "yes"}).status_code == 303
    signed_in = client_for(manager)
    assert signed_in.get("/plugins/test/").json() == {"private": True}
    assert signed_in.get("/plugins/test/static/style.css").text == "static"
    assert signed_in.head("/plugins/test/static/style.css").status_code == 200


def test_public_assets_and_not_found_pages(manager, public_plugin):
    manager.change("test", "enable")
    anonymous = client_for(manager, authenticated=False)
    assert anonymous.get("/plugins/test/static/style.css").text == "static"
    assert anonymous.head("/plugins/test/static/style.css").status_code == 200
    assert anonymous.get("/plugins/test/static/missing.css").status_code == 404
    assert anonymous.get("/plugins/test/static/%2e%2e/plugin.py").status_code == 404
    api_missing = anonymous.get("/plugins/test/public/missing")
    assert api_missing.status_code == 404
    assert api_missing.json() == {"detail": "Not Found"}
    page_missing = anonymous.get("/plugins/test/public/missing", headers={"accept": "text/html"})
    assert page_missing.status_code == 404
    assert "Page not found" in page_missing.text
    manager.change("test", "disable")
    disabled = anonymous.get("/plugins/test/public/", headers={"accept": "text/html"})
    assert disabled.status_code == 404
    assert "Page not found" in disabled.text


def test_public_routes_follow_plugin_lifecycle(manager, public_plugin):
    manager.change("test", "enable")
    anonymous = client_for(manager, authenticated=False)
    (public_plugin / "templates/survey.html").write_text("Updated survey")
    (public_plugin / "static/style.css").write_text("updated")
    manager.change("test", "reload")
    assert anonymous.get("/plugins/test/public/").text == "Updated survey"
    assert anonymous.get("/plugins/test/static/style.css").text == "updated"
    (public_plugin / "plugin.py").write_text("def setup(api):\n    raise ValueError('broken')\n")
    with pytest.raises(ValueError, match="broken"):
        manager.change("test", "reload")
    assert anonymous.get("/plugins/test/public/").status_code == 200
    manager.change("test", "disable")
    assert anonymous.get("/plugins/test/public/").status_code == 404
    assert anonymous.get("/plugins/test/static/style.css").status_code == 404


def test_private_routes_cannot_use_reserved_public_prefix(manager, tmp_path):
    path = source(tmp_path, '''
def setup(api):
    @api.public_router.get("/")
    def landing():
        return {}

    @api.router.get("/public/admin")
    def admin():
        return {}
''')
    manager.install(str(path))
    with pytest.raises(ValueError, match="/public is reserved.*/public/admin"):
        manager.change("test", "enable")


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
    # Router-gate unit test with valid role vocabulary (admin/member only).
    # The full middleware + real-account perimeter is covered by the
    # multi-user HTTP suite; this isolates the route-level admin gate.
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"id": "alice", "role": "member", "enabled": True}
    )
    assert (
        client.post(
            "/api/plugins/install", json={"path": str(source(tmp_path))}
        ).status_code
        == 403
    )
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"id": "alice", "role": "admin", "enabled": True}
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
        store, "get_user", lambda uid: {"id": "alice", "role": "member", "enabled": True}
    )
    assert client.get("/api/plugins/ideas").status_code == 403
    monkeypatch.setattr(
        store, "get_user", lambda uid: {"id": "alice", "role": "admin", "enabled": True}
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
    from app.runtime.tools.user_ctx import bind_user, reset_user
    from app.services import store
    from tests.fakes.access import owned_admin_scope

    # Real Member account: the tool's role check denies before any install.
    member = store.create_user({"username": "plugmember", "password": "password1", "role": "member"})
    token = bind_user(member["id"])
    try:
        assert run({"action": "install", "path": str(source(tmp_path))}).startswith(
            "Error:"
        )
    finally:
        reset_user(token)
    assert manager.list() == []
    # Real Admin identity authorizes; the install itself proceeds.
    with owned_admin_scope():
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


def test_home_cards_and_starters_contribute_per_user(manager, tmp_path):
    path = source(
        tmp_path,
        """
import time
from app.runtime.tools.user_ctx import current_user_id

def setup(api):
    api.page("/", "Overview")
    api.home_card(lambda uid: {"metric": {"value": uid, "label": current_user_id()}}, title="Spend", size="m", kanji="金", id="spending")
    api.home_card(lambda uid: 1 / 0)
    api.starter("Check spend", "How much did I spend this week?")
""",
    )
    manager.install(str(path))
    manager.change("test", "enable")
    out = manager.home_contributions("usr_alice")
    good, broken = out["cards"]
    assert good == {
        "plugin": "test",
        "key": "test:spending",
        "default_visible": True,
        "title": "Spend",
        "size": "m",
        "kanji": "金",
        "data": {"metric": {"value": "usr_alice", "label": "usr_alice"}},
    }
    assert broken["key"] == "test:1" and broken["error"] == "Card failed to load"
    assert out["starters"] == [
        {"label": "Check spend", "prompt": "How much did I spend this week?", "plugin": "test"}
    ]
    manager.change("test", "disable")
    assert manager.home_contributions("usr_alice") == {"cards": [], "starters": []}


def test_slow_home_card_times_out(manager, tmp_path):
    path = source(
        tmp_path,
        """
import threading
gate = threading.Event()

def setup(api):
    api.home_card(lambda uid: gate.wait(5) and {})
    api.on_dispose(gate.set)
""",
    )
    manager.install(str(path))
    manager.change("test", "enable")
    out = manager.home_contributions("usr_alice", timeout=0.05)
    assert out["cards"][0]["error"] == "Timed out"


def test_home_card_and_starter_validation(tmp_path):
    from app.plugins.sdk import PluginAPI

    api = PluginAPI("x", tmp_path, tmp_path)

    async def coro(uid):
        return {}

    with pytest.raises(ValueError):
        api.home_card(coro)
    with pytest.raises(ValueError):
        api.home_card(lambda uid: {}, size="xl")
    for bad in ("", "ab", "A", "😀", 7):
        with pytest.raises(ValueError):
            api.home_card(lambda uid: {}, kanji=bad)
    for bad in (True, 0, 4, 3601, 10.5, "10"):
        with pytest.raises(ValueError, match="refresh_seconds"):
            api.home_card(lambda uid: {}, refresh_seconds=bad)
    api.home_card(lambda uid: {}, size="l", kanji="板")
    api.home_card(lambda uid: {}, id="budgets")
    assert api.home_cards[-1]["default_visible"] is False
    with pytest.raises(ValueError):
        api.home_card(lambda uid: {}, id="budgets")
    with pytest.raises(ValueError):
        api.home_card(lambda uid: {}, id="Bad ID")
    for i in range(10):
        api.home_card(lambda uid: {}, id=f"widget-{i}")
    with pytest.raises(ValueError):
        api.home_card(lambda uid: {})
    with pytest.raises(ValueError):
        api.starter("", "prompt")
    with pytest.raises(ValueError):
        api.starter("label", "x" * 501)
    for i in range(4):
        api.starter(f"s{i}", "go")
    with pytest.raises(ValueError):
        api.starter("s5", "go")


def test_home_card_refresh_api_filters_and_scopes(manager, tmp_path, monkeypatch):
    from app.services import store
    from tests.fakes.access import admin_client

    manager.install(str(source(tmp_path, """
def setup(api):
    api.home_card(lambda uid: {"metric": {"value": uid}, "html": "unsafe"},
                  id="monitor", refresh_seconds=10)
    api.home_card(lambda uid: 1 / 0, id="static")
    api.home_card(lambda uid: 1 / 0, id="other", refresh_seconds=30)
""")))
    manager.change("test", "enable")
    # Two real Admin logins against the production app: per-user card data
    # with no spoofed session_user_id or request-state identity. Card refresh
    # is Admin-only (plugins run with server authority until per-user plugin
    # boundaries exist), so Members are not exercised here.
    store.rebind(tmp_path / "home-cards-identity.db")
    client, alice = admin_client(username="cardalice", password="password1", role="admin")
    bob_client, bob = admin_client(username="cardbob", password="password1", role="admin")
    assert manager.home_contributions(alice["id"])["cards"][0]["refresh_seconds"] == 10
    response = client.get("/api/home/cards", params=[
        ("keys", "test:monitor"), ("keys", "test:static"), ("keys", "missing:0"),
    ])
    assert response.status_code == 200
    cards = response.json()["cards"]
    assert [c["key"] for c in cards] == ["test:monitor"]
    assert cards[0]["refresh_seconds"] == 10
    assert cards[0]["data"] == {"metric": {"value": alice["id"], "label": ""}}
    assert bob_client.get("/api/home/cards?keys=test:monitor").json()["cards"][0]["data"]["metric"]["value"] == bob["id"]
    assert client.get("/api/home/cards?keys=test:other").json()["cards"][0]["error"] == "Card failed to load"
    assert client.get("/api/home/cards").status_code == 422
    manager.change("test", "disable")
    assert client.get("/api/home/cards?keys=test:monitor").json() == {"cards": []}
