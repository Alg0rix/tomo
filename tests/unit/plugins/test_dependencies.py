import json
from pathlib import Path
import sys

import pytest

from app.plugins import dependencies
from app.plugins.dependencies import DependencyEnvironment, requirements, status
from app.plugins.manager import PluginManager


def package(tmp_path, text="demo-lib>=1"):
    path = tmp_path / "plugin"
    path.mkdir()
    (path / "requirements.txt").write_text(text)
    (path / "tomo-plugin.json").write_text(
        json.dumps({"id": "demo", "name": "Demo", "version": "1", "sdk_version": 1})
    )
    (path / "plugin.py").write_text(
        'raise AssertionError("must not import during sync")'
    )
    return path


def test_read_requirements_and_pyproject(tmp_path):
    path = package(
        tmp_path, '# Server packages\ndemo-lib>=1\nother-lib; python_version < "2"'
    )
    assert len(requirements(path)) == 2
    (path / "requirements.txt").unlink()
    (path / "pyproject.toml").write_text('[project]\ndependencies=["demo-lib>=2"]')
    assert str(requirements(path)[0]) == "demo-lib>=2"


@pytest.mark.parametrize(
    "text",
    [
        "--index-url https://example.org",
        "-r other.txt",
        "demo @ https://example.org/a.whl",
        "../local",
    ],
)
def test_executable_or_external_requirements_rejected(tmp_path, text):
    path = package(tmp_path, text)
    assert status(path)["status"] == "invalid"


def test_missing_version_and_marker_status(tmp_path, monkeypatch):
    path = package(tmp_path, 'demo-lib>=2\nignored; python_version < "2"')
    monkeypatch.setattr(dependencies, "installed", lambda paths=None: {"demo-lib": "1"})
    assert status(path)["missing"] == ["demo-lib>=2"]
    monkeypatch.setattr(dependencies, "installed", lambda paths=None: {"demo-lib": "2"})
    assert status(path)["status"] == "ready"


def test_enable_reports_declared_missing_dependencies(tmp_path, monkeypatch):
    path = package(tmp_path)
    monkeypatch.setattr(dependencies, "installed", lambda paths=None: {})
    manager = PluginManager(tmp_path / "home")
    manager.install(str(path))
    assert manager.list()[0]["dependencies"]["status"] == "missing"
    with pytest.raises(ValueError, match="Sync dependencies"):
        manager.change("demo", "enable")


def fake_uv(monkeypatch, fail=False):
    calls = []
    monkeypatch.setattr(dependencies.shutil, "which", lambda command: "/usr/bin/uv")
    monkeypatch.setattr(dependencies, "installed", lambda paths=None: {"core-lib": "3"})

    def run(command):
        calls.append(command)
        assert "--python" in command and sys.executable in command
        assert "--only-binary" in command and "--no-config" in command
        if fail:
            raise ValueError("resolution failed")
        if "compile" in command:
            Path(command[command.index("-o") + 1]).write_text(
                "demo-lib==1\ncore-lib==3\n"
            )
            constraint = Path(command[command.index("-c") + 1])
            assert "core-lib==3" in constraint.read_text()
        else:
            assert "--no-deps" in command
            wheel = Path(command[command.index("-r") + 1]).read_text()
            assert wheel == "demo-lib==1\n"
            assert "core-lib" not in wheel

    monkeypatch.setattr(DependencyEnvironment, "_run", staticmethod(run))
    return calls


def test_persistent_uv_overlay_excludes_core_and_does_not_enable(tmp_path, monkeypatch):
    path = package(tmp_path)
    calls = fake_uv(monkeypatch)
    manager = PluginManager(tmp_path / "home")
    manager.install(str(path))
    result = manager.sync_dependencies("demo")
    assert not result["enabled"]
    assert not manager.list()[0]["running"]
    assert len(calls) == 2
    assert manager.dependencies.state.exists()
    active = manager.dependencies.active
    assert active in sys.path and str(tmp_path / "home/plugins/dependencies") in active
    restored = DependencyEnvironment(tmp_path / "home")
    assert restored.active == active
    while active in sys.path:
        sys.path.remove(active)


def test_failed_resolution_keeps_previous_environment(tmp_path, monkeypatch):
    path = package(tmp_path)
    fake_uv(monkeypatch, fail=True)
    manager = PluginManager(tmp_path / "home")
    manager.install(str(path))
    with pytest.raises(ValueError, match="resolution failed"):
        manager.sync_dependencies("demo")
    assert not manager.dependencies.state.exists()
    assert not list(manager.dependencies.root.glob("env-*"))


def test_changed_registry_during_sync_rejects_prepared_environment(
    tmp_path, monkeypatch
):
    path = package(tmp_path)
    fake_uv(monkeypatch)
    manager = PluginManager(tmp_path / "home")
    manager.install(str(path))
    original = manager.dependencies.prepare

    def prepare(paths):
        row = original(paths)
        manager.change("demo", "uninstall")
        return row

    monkeypatch.setattr(manager.dependencies, "prepare", prepare)
    with pytest.raises(RuntimeError, match="changed during dependency sync"):
        manager.sync_dependencies("demo")
    assert not manager.dependencies.state.exists()
    assert not list(manager.dependencies.root.glob("env-*"))


def test_python_change_requires_resync(tmp_path):
    env = DependencyEnvironment(tmp_path / "home")
    site = env.root / "env-one/site-packages"
    site.mkdir(parents=True)
    env.state.write_text(
        json.dumps(
            {"directory": "env-one/site-packages", "python": [1, 0], "resolved": {}}
        )
    )
    restored = DependencyEnvironment(tmp_path / "home")
    assert not restored.active and "Python changed" in restored.error


def test_dependency_api_and_agent_require_admin_and_leave_disabled(
    tmp_path, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware
    from app.plugins import manager as module
    from app.plugins.routes import router
    from app.runtime.tools.plugin_manager import run
    from app.services import store

    path = package(tmp_path)
    fake_uv(monkeypatch)
    manager = PluginManager(tmp_path / "home")
    monkeypatch.setattr(module, "_manager", manager)
    manager.install(str(path))
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
    assert client.post("/api/plugins/demo/sync-dependencies").status_code == 403
    assert run({"action": "sync_dependencies", "id": "demo"}).startswith("Error:")
    role["role"] = "admin"
    assert (
        client.post(
            "/api/plugins/demo/sync-dependencies",
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    try:
        assert (
            client.post("/api/plugins/demo/sync-dependencies").json()["enabled"]
            is False
        )
        assert (
            json.loads(run({"action": "sync_dependencies", "id": "demo"}))["enabled"]
            is False
        )
    finally:
        for path in tuple(sys.path):
            if str(manager.dependencies.root) in path:
                sys.path.remove(path)


def test_undeclared_cv2_error_explains_distribution_name(tmp_path):
    path = package(tmp_path, "")
    (path / "plugin.py").write_text('raise ModuleNotFoundError("no cv2",name="cv2")')
    manager = PluginManager(tmp_path / "home")
    manager.install(str(path))
    with pytest.raises(ValueError, match="opencv-python-headless"):
        manager.change("demo", "enable")


@pytest.mark.parametrize("loaded", [False, True])
def test_overlay_versions_are_protected_only_after_import(
    tmp_path, monkeypatch, loaded
):
    import types

    path = package(tmp_path)
    calls = fake_uv(monkeypatch)
    monkeypatch.setattr(
        dependencies,
        "installed",
        lambda paths=None: (
            {"core-lib": "3", "demo-lib": "1"} if paths is None else {"core-lib": "3"}
        ),
    )
    monkeypatch.setattr(
        dependencies.metadata,
        "packages_distributions",
        lambda: {"demo_import": ["demo-lib"]},
    )
    if loaded:
        monkeypatch.setitem(sys.modules, "demo_import", types.ModuleType("demo_import"))
    env = DependencyEnvironment(tmp_path / "home")
    candidate = env.prepare([path])
    constraint = Path(calls[0][calls[0].index("-c") + 1]).read_text()
    assert ("demo-lib==1" in constraint) is loaded
    env.discard(candidate)
