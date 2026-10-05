"""Stage 3: Member restricted container terminals + Telegram self-service.

Real seams only: HTTP API, terminal WebSockets, live Docker containers and
SQLite. No mocked authorization, mounts or PTYs.
"""
import os
import subprocess
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import store

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Terminals require POSIX")
ORIGIN = {"origin": "http://testserver"}


_peer_counter = 0


def member_client(**kwargs):
    # Distinct peer names so the process-global password-login brute-force
    # guard treats each test client as its own IP bucket (established
    # tests/fakes/access.py convention). Production guard untouched.
    global _peer_counter
    _peer_counter += 1
    return TestClient(app, client=(f"mtermtest-{_peer_counter}", 50000), **kwargs)


def login(client, name):
    response = client.post(
        "/login",
        data={"username": name, "password": "password1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def docker_available():
    runtime = os.environ.get("TOMO_SANDBOX_RUNTIME", "docker")
    image = os.environ.get("TOMO_SANDBOX_IMAGE", "tomo:sandbox")
    try:
        subprocess.run([runtime, "info"], capture_output=True, check=True, timeout=10)
        subprocess.run([runtime, "image", "inspect", image], capture_output=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


@pytest.fixture
def world(tmp_path):
    if not docker_available():
        pytest.skip("REAL container acceptance not verified: local runtime or image unavailable")
    from tests.fakes.access import ensure_stoppers
    store.rebind(tmp_path / "member-terminals.db")
    ensure_stoppers()
    tag = uuid.uuid4().hex[:8]
    alice = store.create_user({"username": f"mterm_alice_{tag}", "password": "password1", "role": "member"})
    bob = store.create_user({"username": f"mterm_bob_{tag}", "password": "password1", "role": "member"})
    profile = store.create_llm_profile({"name": f"Assigned {tag}", "model": "local-test"})
    for uid in (alice["id"], bob["id"]):
        store.access.assign("usr_admin", uid, "model", profile["id"])
    personal = store.access.ensure_personal_space(alice["id"])
    import os as _os
    fs = _os.statvfs(store.get_workplace(personal["id"])["root_path"])
    capacity = (fs.f_blocks * fs.f_frsize // (1024 * 1024)) + 4096
    for uid in (alice["id"], bob["id"]):
        store.access.set_quota("usr_admin", uid, {"disk_mb": capacity, "memory_mb": 2048})
    sid = store.create_swarm_session(["main"], user_id=alice["id"])
    store.access.set_chat_access(alice["id"], sid, personal["id"])
    yield alice, bob, sid, personal
    # Per-test deletes plus app-lifespan stoppers own cleanup. No fixture
    # close_all: terminal loops belong to each test's TestClient portal and
    # cannot be joined from a foreign loop here.


def output_until(ws, marker, timeout=30):
    output = b""
    deadline = time.monotonic() + timeout
    while marker not in output and time.monotonic() < deadline:
        message = ws.receive()
        if message.get("bytes"):
            output += message["bytes"]
    assert marker in output, f"marker {marker!r} missing in {output[-300:]!r}"
    return output


def test_member_restricted_terminal_io_mounts_and_no_host_fallback(world):
    alice, _, sid, personal = world
    from app.services.terminals import terminal_manager
    assert terminal_manager.get(sid, "nope", user_id=alice["id"]) is None
    base = f"/api/sessions/{sid}/terminals"
    with member_client() as client:
        login(client, alice["username"])
        listed = client.get(base).json()
        assert listed["backend"] == "container"
        assert listed["cwd"] == f"/workplaces/{personal['id']}"
        created = client.post(base, json={})
        assert created.status_code == 201, created.text
        info = created.json()
        assert info["backend"] == "container"
        assert info["cwd"] == f"/workplaces/{personal['id']}"
        tid = info["id"]
        url = base + "/" + tid + "/ws"
        with client.websocket_connect(url, headers=ORIGIN) as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "input", "data": "echo HELLO_STAGE3; pwd; echo HOME_IS_$HOME; echo END_$((40+2))\r"})
            out = output_until(ws, b"END_42")
            assert b"HELLO_STAGE3" in out
            assert f"/workplaces/{personal['id']}".encode() in out
            assert b"HOME_IS_/home/chat" in out
            # Same authorized mounts as actions, private home, no host secrets.
            ws.send_json({"type": "input", "data": "test -e /app/main.py && echo HOST_LEAK || echo NO_HOST_APP; env | grep -i -E 'secret|token' || echo NO_SECRETS; echo END_$((41+2))\r"})
            out = output_until(ws, b"END_43")
            assert b"NO_HOST_APP" in out
            assert b"NO_SECRETS" in out
            # Writable active folder works through the held shell.
            ws.send_json({"type": "input", "data": "echo terminal-data > term-note.txt; cat term-note.txt; echo END_$((42+2))\r"})
            out = output_until(ws, b"END_44")
            assert b"terminal-data" in out
        # Detach keeps the PTY: reattach sees the same PID and scrollback.
        assert client.get(base).json()["terminals"][0]["pid"] == info["pid"]
        with client.websocket_connect(url, headers=ORIGIN) as ws:
            assert ws.receive_json()["pid"] == info["pid"]
            assert b"terminal-data" in ws.receive_bytes()
        assert client.delete(base + "/" + tid).json()["success"] is True
        assert client.get(base).json()["terminals"] == []
        with pytest.raises(Exception):
            with client.websocket_connect(url, headers=ORIGIN):
                pass


def test_member_terminal_readonly_mount_rejects_writes(world):
    alice, bob, sid, _ = world
    reference = store.access.create_project(bob["id"], "Stage3 reference")
    store.access.share_project(bob["id"], reference["id"], alice["id"], "read")
    store.access.set_chat_access(alice["id"], sid, store.access.ensure_personal_space(alice["id"])["id"],
                                 [reference["id"]])
    marker = f"/workplaces/{reference['id']}/readonly-probe.txt"
    base = f"/api/sessions/{sid}/terminals"
    with member_client() as client:
        login(client, alice["username"])
        tid = client.post(base, json={}).json()["id"]
        try:
            with client.websocket_connect(base + "/" + tid + "/ws", headers=ORIGIN) as ws:
                ws.receive_json()
                # Read-only input is readable for tool input ...
                ws.send_json({"type": "input", "data": f"echo ref > {marker}; echo WRITE_RC=$?; echo END_$((43+2))\r"})
                out = output_until(ws, b"END_45")
                assert b"WRITE_RC=" in out and b"WRITE_RC=0" not in out
        finally:
            client.delete(base + "/" + tid)


def test_member_terminal_ownership_and_foreign_ids(world):
    alice, bob, sid, _ = world
    bob_sid = store.create_swarm_session(["main"], user_id=bob["id"])
    store.access.set_chat_access(bob["id"], bob_sid, store.access.ensure_personal_space(bob["id"])["id"])
    base = f"/api/sessions/{sid}/terminals"
    bob_base = f"/api/sessions/{bob_sid}/terminals"
    with member_client() as client:
        login(client, alice["username"])
        tid = client.post(base, json={}).json()["id"]
        url = base + "/" + tid + "/ws"
        with client.websocket_connect(url, headers=ORIGIN) as ws:
            ws.receive_json()
            # Foreign session path with a valid nested terminal id stays denied.
            with pytest.raises(Exception):
                with client.websocket_connect(bob_base + "/" + tid + "/ws", headers=ORIGIN):
                    pass
        assert client.get(bob_base).status_code == 404
        login(client, bob["username"])
        assert client.get(base).status_code == 404
        assert client.post(base, json={}).status_code == 404
        assert client.delete(base + "/" + tid).status_code == 404
        with pytest.raises(Exception):
            with client.websocket_connect(url, headers=ORIGIN):
                pass
        # No cookie, no attach: browser-only WS rejects anonymous use.
        client.cookies.clear()
        with pytest.raises(Exception):
            with client.websocket_connect(url, headers=ORIGIN):
                pass
        login(client, alice["username"])
        assert client.delete(base + "/" + tid).status_code == 200


def test_member_terminal_api_key_ownership(world):
    alice, bob, sid, _ = world
    bob_sid = store.create_swarm_session(["main"], user_id=bob["id"])
    store.access.set_chat_access(bob["id"], bob_sid, store.access.ensure_personal_space(bob["id"])["id"])
    base = f"/api/sessions/{sid}/terminals"
    bob_base = f"/api/sessions/{bob_sid}/terminals"
    with member_client() as client:
        login(client, bob["username"])
        # API keys inherit the account: own session works, foreign stays 404.
        token = client.post("/api/api-keys", json={"user_id": bob["id"], "name": "term-key"}).json()["token"]
    auth = {"Authorization": "Bearer " + token}
    with member_client() as keyed:
        assert keyed.get(bob_base, headers=auth).status_code == 200
        assert keyed.get(base, headers=auth).status_code == 404
        created = keyed.post(bob_base, json={}, headers=auth)
        assert created.status_code == 201, created.text
        assert created.json()["backend"] == "container"
        assert keyed.delete(bob_base + "/" + created.json()["id"], headers=auth).status_code == 200


def test_completed_action_does_not_kill_held_terminal(world):
    alice, _, sid, personal = world
    from app.runtime.isolation.backend import backend as container_backend
    base = f"/api/sessions/{sid}/terminals"
    with member_client() as client:
        login(client, alice["username"])
        tid = client.post(base, json={}).json()["id"]
        try:
            with client.websocket_connect(base + "/" + tid + "/ws", headers=ORIGIN) as ws:
                ws.receive_json()
                ws.send_json({"type": "input", "data": "echo TERMINAL_HELD; echo END_$((45+2))\r"})
                assert b"TERMINAL_HELD" in output_until(ws, b"END_47")
                # A one-shot action in the same chat shares the held container
                # and must leave the unrelated active terminal alive.
                context = store.access.resolve_context(alice["id"], sid)
                result = container_backend.execute(context, ["sh", "-c", "echo ACTION_DONE"])
                assert result.returncode == 0 and "ACTION_DONE" in result.stdout
                assert client.get(base).json()["terminals"][0]["id"] == tid
                ws.send_json({"type": "input", "data": "echo STILL_ALIVE; echo END_$((46+2))\r"})
                assert b"STILL_ALIVE" in output_until(ws, b"END_48")
        finally:
            client.delete(base + "/" + tid)


def test_member_unrestricted_terminal_requires_grant_and_keeps_role(world):
    alice, _, sid, personal = world
    base = f"/api/sessions/{sid}/terminals"
    with member_client() as client:
        login(client, alice["username"])
        # Restricted by default: container backend, never host.
        assert client.post(base, json={}).json()["backend"] == "container"
        for row in client.get(base).json()["terminals"]:
            client.delete(base + "/" + row["id"])
        # Explicit activation without a grant is denied, not silently granted.
        with pytest.raises(Exception):
            store.access.set_chat_access(alice["id"], sid, personal["id"], execution_mode="unrestricted",
                                         unrestricted_acknowledged=True)
        store.access.assign("usr_admin", alice["id"], "unrestricted", personal["id"])
        store.access.set_chat_access(alice["id"], sid, personal["id"], execution_mode="unrestricted",
                                     unrestricted_acknowledged=True)
        created = client.post(base, json={})
        assert created.status_code == 201, created.text
        info = created.json()
        assert info["backend"] == "host"
        assert store.get_user(alice["id"])["role"] == "member"
        with client.websocket_connect(base + "/" + info["id"] + "/ws", headers=ORIGIN) as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "input", "data": "echo HOST_GRANTED; echo END_$((44+2))\r"})
            assert b"HOST_GRANTED" in output_until(ws, b"END_46")
        assert client.delete(base + "/" + info["id"]).status_code == 200
        # Revocation denies new terminals; the platform role never changed.
        store.access.revoke("usr_admin", alice["id"], "unrestricted", personal["id"])
        assert store.get_user(alice["id"])["role"] == "member"


def test_container_terminals_do_not_sustain_idle_life(world, monkeypatch):
    import app.services.terminals as terminals

    alice, _, sid, _ = world
    monkeypatch.setattr(terminals, "IDLE_TIMEOUT", 1)
    base = f"/api/sessions/{sid}/terminals"
    with member_client() as client:
        login(client, alice["username"])
        tid = client.post(base, json={}).json()["id"]
        terminal = terminals.terminal_manager.get(sid, tid, user_id=alice["id"])
        assert terminal.backend == "container"
        # 30s-old activity: still busy() by the 60s I/O heuristic, but past
        # the 1s idle timeout. The real supervisor must expire it; with the
        # old circular sustain (busy extends life) it never would.
        terminal.last_activity -= 30
        assert terminal.busy() is True
        deadline = time.monotonic() + 12
        while client.get(base).json()["terminals"] and time.monotonic() < deadline:
            time.sleep(0.2)
        assert client.get(base).json()["terminals"] == []
        from app.runtime.isolation.backend import backend as container_backend
        held = container_backend._environments.get(sid)
        assert held is None or held.held == 0


def test_durable_terminal_admission_counts_against_quota(world):
    from dataclasses import asdict

    alice, _, sid, _ = world
    store.access.set_quota("usr_admin", alice["id"],
                           {**asdict(store.access.get_quota(alice["id"])),
                            "max_concurrent_jobs": 2})
    base = f"/api/sessions/{sid}/terminals"
    with member_client() as client:
        login(client, alice["username"])
        first = client.post(base, json={})
        second = client.post(base, json={})
        assert (first.status_code, second.status_code) == (201, 201)
        # Two durable terminals fill the aggregate quota: a third is denied
        # rather than sharing either slot.
        assert client.post(base, json={}).status_code == 503
        for row in client.get(base).json()["terminals"]:
            assert client.delete(base + "/" + row["id"]).status_code == 200


def test_revoked_session_kills_member_terminal(world):
    alice, _, sid, _ = world
    base = f"/api/sessions/{sid}/terminals"
    with member_client() as client:
        login(client, alice["username"])
        tid = client.post(base, json={}).json()["id"]
        rows = client.get(base).json()["terminals"]
        assert [r["id"] for r in rows] == [tid]
        pid = rows[0]["pid"]
        assert client.delete(f"/api/sessions/{sid}").status_code == 200
        deadline = time.monotonic() + 10
        while client.get(base).status_code != 404 and time.monotonic() < deadline:
            time.sleep(0.1)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_member_telegram_self_service_endpoints(world):
    alice, bob, _, _ = world
    with member_client() as client:
        login(client, alice["username"])
        own_list = f"/api/users/{alice['id']}/telegram"
        own_code = f"/api/users/{alice['id']}/telegram/link-code"
        assert client.get(own_list).json() == {"links": []}
        code = client.post(own_code).json()
        assert code["command"].startswith("/link ")
        # Members cannot manage another account's links.
        assert client.post(f"/api/users/{bob['id']}/telegram/link-code").status_code == 403
        assert client.get(f"/api/users/{bob['id']}/telegram").status_code == 403
        assert client.delete(f"/api/users/{bob['id']}/telegram/42").status_code == 403
        assert client.delete(f"/api/users/{alice['id']}/telegram/99999").status_code == 404


async def test_telegram_group_and_unallowlisted_chats_stay_denied(world):
    alice, _, _, _ = world
    from app.channels.telegram import process_update
    store.update_settings({"telegram_allowed_chat_ids": [4242]})
    client = member_client()
    try:
        login(client, alice["username"])
        code = client.post(f"/api/users/{alice['id']}/telegram/link-code").json()["code"]

        def update(text, *, chat=4242, chat_type="private", sender=4242):
            return {"message": {"chat": {"id": chat, "type": chat_type}, "from": {"id": sender},
                                "message_id": 1, "text": text}}

        # Groups cannot link even when the group id itself is allowlisted.
        store.update_settings({"telegram_allowed_chat_ids": [4242, -100]})
        denied = await process_update(update(f"/link {code}", chat=-100, chat_type="group", sender=7))
        assert "private DM" in denied["reply"]
        # Unknown public chats are denied by default.
        store.update_settings({"telegram_allowed_chat_ids": [4242]})
        refused = await process_update(update("hello", chat=777, sender=777))
        assert refused.get("denied") is True and "not approved" in refused["reply"]
    finally:
        client.close()
