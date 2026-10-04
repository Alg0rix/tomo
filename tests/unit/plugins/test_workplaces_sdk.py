"""Public SDK against real account/workplace storage and the tunnel hub."""

from types import SimpleNamespace

import pytest

from app.plugins.sdk import PluginAPI
from app.runtime.tools.user_ctx import bind_user, reset_user
from app.services.store import Store
from app.workplaces.hub import hub


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    from app import services

    store = Store(tmp_path / "store.db")
    monkeypatch.setattr(services, "store", store)
    admin = store.create_user(
        {"username": "monitoradmin", "password": "password123", "role": "admin"}
    )
    member = store.create_user(
        {"username": "monitoruser", "password": "password123", "role": "member"}
    )
    tunnel = store.create_workplace({"name": "Server", "kind": "tunnel"})
    yield (
        PluginAPI("monitor", tmp_path, tmp_path),
        store,
        admin["id"],
        member["id"],
        tunnel["id"],
    )
    hub.reset()


def test_access_is_rechecked_and_metadata_contains_no_pairing_secrets(sdk):
    api, store, admin, member, wid = sdk
    for uid in (None, "missing", member):
        with pytest.raises(PermissionError):
            api.list_workplaces(user_id=uid)
        with pytest.raises(PermissionError):
            api.exec_workplace(wid, "uptime", user_id=uid)
    token = bind_user(admin)
    try:
        rows = api.list_workplaces()
        assert rows == [api.workplace_status(wid)]
        assert set(rows[0]) == {
            "id",
            "name",
            "kind",
            "online",
            "hostname",
            "version",
            "last_seen_at",
        }
        assert rows[0]["online"] is False
    finally:
        reset_user(token)
    store.update_user(admin, {"enabled": False})
    with pytest.raises(PermissionError):
        api.workplace_status(wid, user_id=admin)


def test_remote_file_read_is_bounded_and_shell_quotes_paths(sdk):
    api, _, admin, _, wid = sdk
    path = "log'; touch /tmp/injected; #"

    def call(method, params, **kwargs):
        import shlex

        assert method == "exec_bash"
        assert params["command"] == f"head -c 5 -- {shlex.quote(path)}"
        return {"ok": True, "result": {"stdout": "abcde", "stderr": "", "exit_code": 0}}

    hub.register(
        SimpleNamespace(workplace_id=wid, call=call, fail_all=lambda reason: None)
    )
    assert api.read_workplace_file(wid, path, max_bytes=4, user_id=admin) == {
        "path": path,
        "content": "abcd",
        "truncated": True,
    }
    with pytest.raises(ValueError):
        api.read_workplace_file(wid, path, max_bytes=1048577, user_id=admin)


def test_explicit_tunnel_execution_preserves_result_and_never_falls_back(sdk):
    api, store, admin, _, wid = sdk
    with pytest.raises(ConnectionError, match="offline"):
        api.exec_workplace(wid, "uptime", user_id=admin)

    # Stand-in for the external connector at the existing hub boundary.
    def call(method, params, **kwargs):
        assert method == "exec_bash"
        assert params == {"command": "uptime", "timeout": 10}
        return {
            "ok": True,
            "result": {"stdout": "", "stderr": "not found", "exit_code": 127},
        }

    hub.register(
        SimpleNamespace(workplace_id=wid, call=call, fail_all=lambda reason: None)
    )
    assert api.workplace_status(wid, user_id=admin)["online"] is True
    assert api.exec_workplace(wid, "uptime", user_id=admin)["exit_code"] == 127
    for timeout in (0, 61, float("nan"), float("inf"), True):
        with pytest.raises(ValueError, match="Timeout"):
            api.exec_workplace(wid, "uptime", timeout=timeout, user_id=admin)
    hub.register(
        SimpleNamespace(
            workplace_id=wid,
            call=lambda *args, **kwargs: {
                "ok": False,
                "error": "connector disconnected",
            },
            fail_all=lambda reason: None,
        )
    )
    with pytest.raises(ConnectionError, match="connector disconnected"):
        api.exec_workplace(wid, "uptime", user_id=admin)
    store.set_workplace_enabled(wid, False)
    assert api.list_workplaces(user_id=admin) == []
    with pytest.raises(ValueError):
        api.exec_workplace(wid, "uptime", user_id=admin)
    local = store.create_workplace(
        {"name": "Local", "kind": "local", "root_path": "/tmp"}
    )
    with pytest.raises(ValueError):
        api.exec_workplace(local["id"], "uptime", user_id=admin)
