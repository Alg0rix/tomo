"""Exercise storage, lifecycle and paid-call boundaries through the public SDK."""

import asyncio
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from app.plugins.sdk import PluginAPI
from app.services.store import Store
from tests.unit.plugins.test_plugins import manager as manager, source


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    from app import services

    store = Store(tmp_path / "store.db")
    monkeypatch.setattr(services, "store", store)
    users = [
        store.create_user({"username": name, "password": "password123"})["id"]
        for name in ("alice", "bob")
    ]
    return store, users


def test_settings_survive_reload_and_isolate_accounts_and_plugins(tmp_path, accounts):
    store, (alice, bob) = accounts
    api = PluginAPI("monitor", tmp_path, tmp_path / "monitor")
    api.settings.set("server", {"id": "one"}, user_id=alice)
    assert api.settings.get("server", user_id=bob) is None
    reloaded = PluginAPI("monitor", tmp_path, tmp_path / "monitor")
    assert reloaded.settings.get("server", user_id=alice) == {"id": "one"}
    assert (
        PluginAPI("other", tmp_path, tmp_path / "other").settings.get(
            "server", user_id=alice
        )
        is None
    )
    reloaded.settings.delete("server", user_id=alice)
    assert api.settings.get("server", "default", user_id=alice) == "default"
    with pytest.raises(ValueError):
        api.settings.set("huge", "x" * 65536, user_id=alice)
    store.update_user(alice, {"enabled": False})
    with pytest.raises(PermissionError):
        api.settings.get("server", user_id=alice)


def test_background_tasks_start_only_after_commit_and_stop_on_reload(
    manager, tmp_path, monkeypatch
):
    from app.plugins import manager as module

    started = threading.Event()
    stopped = threading.Event()
    monkeypatch.setattr(module, "test_started", started, raising=False)
    monkeypatch.setattr(module, "test_stopped", stopped, raising=False)
    path = source(
        tmp_path,
        """
from app.plugins import manager as module
def setup(api):
    def collect(stop):
        module.test_started.set()
        stop.wait(3)
        module.test_stopped.set()
    api.background_task(collect, interval_seconds=1)
""",
    )
    manager.install(str(path))
    save = manager._save
    monkeypatch.setattr(
        manager, "_save", lambda: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError):
        manager.change("test", "enable")
    assert not started.is_set()
    monkeypatch.setattr(manager, "_save", save)
    manager.change("test", "enable")
    assert started.wait(2)
    (path / "plugin.py").write_text("def setup(api): pass\n")
    manager.change("test", "reload")
    assert stopped.is_set()
    # Reloading to a failing setup keeps the existing instance; its worker
    # registration must not execute during speculative setup.
    started.clear()
    (path / "plugin.py").write_text("""
from app.plugins import manager as module
def setup(api):
    api.background_task(lambda stop: module.test_started.set())
    raise ValueError("bad setup")
""")
    with pytest.raises(ValueError):
        manager.change("test", "reload")
    assert not started.is_set()


def test_background_shutdown_restart_and_disable(manager, tmp_path, monkeypatch):
    from app.plugins import manager as module

    started, stopped = threading.Event(), threading.Event()
    monkeypatch.setattr(module, "test_started", started, raising=False)
    monkeypatch.setattr(module, "test_stopped", stopped, raising=False)
    path = source(
        tmp_path,
        """
from app.plugins import manager as module
def setup(api):
    def collect(stop):
        module.test_started.set()
        stop.wait(3)
        module.test_stopped.set()
    api.background_task(collect)
""",
    )
    manager.install(str(path))
    manager.change("test", "enable")
    assert started.wait(2)
    manager.close()
    assert stopped.is_set()
    started.clear()
    stopped.clear()
    manager.start()
    assert started.wait(2)
    manager.change("test", "disable")
    assert stopped.is_set()


def test_notification_targets_are_opaque_owned_and_reauthorize(
    tmp_path, accounts, monkeypatch
):
    from app.channels import delivery

    monkeypatch.setattr(delivery, "_channels", {})
    from app.channels.delivery import DeliveryBlocked, register_delivery_channel
    from app.runtime.artifacts.fs import bind_session, reset_session

    store, (alice, bob) = accounts
    sid = store.get_or_create_session("main", alice)
    api = PluginAPI("monitor", tmp_path, tmp_path / "monitor")
    sent = []

    class Channel:
        allowed = True

        def capture_current(self):
            return {"user_id": alice, "destination": "private"}

        @asynccontextmanager
        async def open(self, target, session_id):
            if not self.allowed:
                raise DeliveryBlocked("revoked")
            assert session_id == sid
            assert target["destination"] == "private"
            yield self

        async def send_final(self, content, *, delivery_id):
            sent.append(content)
            return {"message_id": 1}

    channel = Channel()
    register_delivery_channel("test-plugin-" + api.path.name, channel)
    binding = bind_session(sid)
    try:
        token = api.capture_notification_target(user_id=alice)
        with pytest.raises(PermissionError):
            api.capture_notification_target(user_id=bob)
    finally:
        reset_session(binding)
    with pytest.raises(PermissionError):
        asyncio.run(api.notify(token, "down", user_id=bob))
    assert asyncio.run(api.notify(token, "down", user_id=alice)) == {"message_id": 1}
    assert sent == ["down"]
    channel.allowed = False
    with pytest.raises(DeliveryBlocked):
        asyncio.run(api.notify(token, "down", user_id=alice))
    store.delete_session(sid)
    with pytest.raises(PermissionError):
        asyncio.run(api.notify(token, "down", user_id=alice))


def test_generate_uses_real_profile_wire_limits_and_usage(
    tmp_path, accounts
):
    store, (alice, _) = accounts
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            if requests[-1]["messages"][0]["content"] == "slow":
                time.sleep(0.2)
            body = json.dumps(
                {
                    "choices": [
                        {"message": {"content": "summary"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        profile = store.create_llm_profile(
            {
                "name": "Plugin test",
                "model": "test",
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "api_key": "test",
            }
        )
        api = PluginAPI("monitor", tmp_path, tmp_path / "monitor")
        result = asyncio.run(
            api.generate(
                "Summarize metrics",
                profile_id=profile["id"],
                max_output_tokens=123,
                user_id=alice,
            )
        )
        assert result == {
            "content": "summary",
            "prompt_tokens": 12,
            "completion_tokens": 3,
        }
        assert requests[0]["max_tokens"] == 123
        assert "tools" not in requests[0]
        usage = store.with_db(
            lambda conn: dict(
                conn.execute(
                    "SELECT * FROM usage_events WHERE agent_id='plugin:monitor'"
                ).fetchone()
            )
        )
        assert usage["prompt_tokens"] == 12 and usage["completion_tokens"] == 3
        assert usage["turns"] == 0
        with pytest.raises(TimeoutError):
            asyncio.run(
                api.generate(
                    "slow", profile_id=profile["id"], timeout=0.05, user_id=alice
                )
            )
        with pytest.raises(ValueError):
            asyncio.run(
                api.generate("x" * 32769, profile_id=profile["id"], user_id=alice)
            )
        from app.runtime.llm import LLMConfigError

        def subscription(conn):
            conn.execute(
                "UPDATE llm_profiles SET auth_mode='subscription' WHERE id=?",
                (profile["id"],),
            )
            conn.commit()

        store.with_db(subscription)
        with pytest.raises(LLMConfigError, match="subscription output limits"):
            asyncio.run(api.generate("again", profile_id=profile["id"], user_id=alice))
        store.update_user(alice, {"enabled": False})
        with pytest.raises(PermissionError):
            asyncio.run(api.generate("again", user_id=alice))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_agent_starts_an_owned_turn_and_schedule_stays_on_that_account(tmp_path, accounts, monkeypatch):
    store, (alice, _) = accounts
    admin = "usr_admin"
    started = {}

    class Turn:
        def unsubscribe(self, _queue):
            return None

    async def fake_start(session_id, message, user_id, *args, **kwargs):
        started.update(
            session_id=session_id, message=message, user_id=user_id, origin=kwargs.get("origin")
        )
        return Turn(), None

    monkeypatch.setattr("app.services.chat.start_session_turn", fake_start)
    api = PluginAPI("money", tmp_path, tmp_path / "money")
    result = asyncio.run(api.agent("Review the inbox", user_id=admin))
    assert result["status"] == "started"
    assert result["agent_id"]
    assert started["user_id"] == admin
    assert started["message"] == "Review the inbox"
    assert started["origin"] == "plugin"
    assert store.get_session(result["session_id"])["user_id"] == admin
    with pytest.raises(ValueError, match="1–32768"):
        asyncio.run(api.agent("   ", user_id=admin))
    store.update_user(alice, {"enabled": False})
    with pytest.raises(PermissionError):
        asyncio.run(api.agent("Review the inbox", user_id=alice))

    from app.plugins.services import turn_hook

    with turn_hook():
        with pytest.raises(RuntimeError, match="on_turn_end"):
            asyncio.run(api.agent("Review the inbox", user_id=admin))

    routine = api.schedule(
        "Inbox review", "Review pending captures", when="every 30m", user_id=admin
    )
    assert routine["name"] == "money: Inbox review"
    assert routine["enabled"] is True
    listed = api.schedules(user_id=admin)
    assert [row["id"] for row in listed] == [routine["id"]]
    other = PluginAPI("other", tmp_path, tmp_path / "other")
    with pytest.raises(PermissionError):
        other.unschedule(routine["id"], user_id=admin)
    api.unschedule(routine["id"], user_id=admin)
    assert api.schedules(user_id=admin) == []
    assert store.get_schedule(routine["id"]) is None
