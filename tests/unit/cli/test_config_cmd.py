"""CLI changes persist in the same DB without booting server recovery."""

import json
import os
import subprocess
import sys

import pytest

from app.core import config
from app.services import store
from cli.__main__ import _run


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "cli.db"
    store.rebind(path)
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def invoke(capsys, *argv):
    assert _run(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


def test_tunnel_create_pair_and_reuse(db, capsys):
    wp = invoke(capsys, "workplaces", "create", "server-x", "--json")
    assert wp["kind"] == "tunnel"
    assert wp["pairing_code"]
    assert wp["online"] is None
    paired = store.pair_connector(wp["pairing_code"], hostname="server-x")
    assert paired["workplace_id"] == wp["id"]
    fresh = invoke(capsys, "workplaces", "pairing-code", wp["id"], "--json")
    assert fresh["pairing_code"]
    assert len([w for w in store.list_workplaces() if w["id"] == wp["id"]]) == 1


def test_agent_workplace_and_tools(db, capsys):
    wp = invoke(
        capsys,
        "config",
        "workplaces",
        "create",
        "--set",
        "name=remote",
        "--set",
        "kind=tunnel",
    )
    agent = invoke(
        capsys,
        "config",
        "agents",
        "create",
        "--set",
        "name=CLI Agent",
        "--set",
        f"workplace_id={wp['id']}",
    )
    invoke(
        capsys,
        "config",
        "agent-tools",
        "update",
        agent["id"],
        "--data",
        '{"enabled":{"bash":true}}',
    )
    assert "bash" in store.get_enabled_tool_ids(agent["id"])
    updated = invoke(
        capsys, "config", "agents", "update", agent["id"], "--set", "enabled=false"
    )
    assert not updated["enabled"]
    invoke(capsys, "config", "agents", "delete", agent["id"])
    assert store.get_agent(agent["id"]) is None


def test_secrets_are_encrypted_and_not_printed(db, capsys):
    secret = "secret-never-echo-this"
    profile = invoke(
        capsys,
        "config",
        "llm-profiles",
        "create",
        "--data",
        json.dumps(
            {
                "name": "CLI model",
                "base_url": "https://example.com/v1",
                "model": "test",
                "api_key": secret,
            }
        ),
    )
    assert secret not in json.dumps(profile)
    store.set_default_llm_profile(profile["id"])
    assert store.resolve_llm_profile(None)["api_key"] == secret
    result = invoke(
        capsys, "config", "settings", "update", "--set", f"telegram_bot_token={secret}"
    )
    assert secret not in json.dumps(result)
    assert store.get_settings()["telegram_bot_token"] == secret


def test_mcp_user_and_schedule_configuration(db, capsys):
    mcp = invoke(
        capsys,
        "config",
        "mcp-servers",
        "create",
        "--data",
        '{"name":"cli mcp","transport":"stdio","command":"echo","args":["hi"]}',
    )
    assert store.get_mcp_server(mcp["id"])["command"] == "echo"
    user = invoke(
        capsys,
        "config",
        "users",
        "create",
        "--data",
        '{"username":"cli_user","password":"good-password"}',
    )
    key = invoke(
        capsys, "config", "api-keys", "create", "--set", f"user_id={user['id']}"
    )
    assert store.authenticate_api_key(key["token"])["user_id"] == user["id"]
    invoke(capsys, "config", "api-keys", "delete", key["id"])
    assert store.authenticate_api_key(key["token"]) is None
    agent = store.get_coordinator()
    schedule = invoke(
        capsys,
        "config",
        "schedules",
        "create",
        "--data",
        json.dumps(
            {
                "name": "CLI schedule",
                "agent_id": agent["id"],
                "schedule": "every 1h",
                "message": "check",
            }
        ),
    )
    invoke(capsys, "config", "schedules", "pause", schedule["id"])
    assert not store.get_schedule(schedule["id"])["enabled"]


def test_errors_leave_existing_configuration_unchanged(db, capsys):
    assert _run(["config", "settings", "update", "--set", "made_up_setting=true"]) == 1
    assert "made_up_setting" not in store.get_settings()
    assert (
        _run(
            [
                "config",
                "agents",
                "create",
                "--set",
                "name=test",
                "--set",
                "workplace_id=missing",
            ]
        )
        == 1
    )
    assert (
        _run(
            [
                "config",
                "llm-profiles",
                "create",
                "--data",
                '{"name":"bad","api_key":{"secret":"dont-print-me"}}',
            ]
        )
        == 1
    )
    assert "dont-print-me" not in capsys.readouterr().err
    admin = store.get_user_by_username("admin")
    assert _run(["config", "users", "delete", admin["id"]]) == 1
    assert store.get_user(admin["id"])


def test_cli_does_not_initialize_server_store(db):
    env = dict(os.environ, TOMO_DB_PATH=str(db))
    code = """import sys
from cli.__main__ import _run
from cli.config_cmd import RESOURCES
commands = [["config", resource, "list"] for resource in RESOURCES]
commands += [["config", "settings", "show"], ["config", "knowledge", "create", "--set", "title=test", "--set", "body=test"], ["workplaces", "list"], ["skills", "sync"]]
for argv in commands:
    assert _run(argv) == 0
assert "app.services.store" not in sys.modules, "CLI booted the server store"
"""
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
