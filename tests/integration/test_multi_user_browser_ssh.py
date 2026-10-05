"""Browser parity + restricted SSH parity (real seams, no live external hosts).

* Offline browser: real ``tomo:sandbox`` container, real Python
  cloakbrowser ``launch()`` workflow as a Member, non-root read-only
  container with a private ``/home/chat`` cache. Skips explicitly without
  the image (same convention as the isolation suite).
* Online browser: loopback fixture HTTP server + ``TOMO_BROWSER_TEST_ORIGIN``
  exact-origin allowance + real ``network_egress=scoped`` setting. Proves
  the initial URL, every redirect hop, subresource/websocket policy and
  loopback denial outside the allowance — production SSRF untouched.
* Restricted SSH: local destination subprocesses running the real
  stdlib agent (:mod:`app.workplaces.ssh_agent`) through the real
  :mod:`app.workplaces.ssh_contract` (envelope, generation, scopes,
  read-only mounts, deadlines, teardown) with the real SQLite policy.
  No live external hosts, no credentials.
"""
from __future__ import annotations

import os
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.runtime.access import AccessDenied, AccessUnavailable, execution_scope

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Browser/SSH destinations require POSIX")


def _tag(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def world(tmp_path, monkeypatch):
    from tests.fakes.access import ensure_stoppers

    from app.services import store
    from app.workplaces import ssh_contract

    monkeypatch.setenv("TOMO_BROWSER_TEST_ORIGIN", "")
    store.rebind(tmp_path / "bssh.db")
    ssh_contract.reset()
    ensure_stoppers()
    db = store
    tag = uuid.uuid4().hex[:8]
    admin = db.create_user({"username": f"bssh_admin_{tag}", "password": "password1", "role": "admin"})
    alice = db.create_user({"username": f"bssh_alice_{tag}", "password": "password1", "role": "member"})
    profile = db.create_llm_profile({"name": f"Assigned {tag}", "model": "local-test"})
    db.access.assign(admin["id"], alice["id"], "model", profile["id"])
    sid = db.create_home_session(alice["id"])["session_id"]
    yield db, admin, alice, sid
    ssh_contract.reset()


def _grant(db, admin, alice_id, wid, permission="read_write"):
    db.access.assign(admin["id"], alice_id, "workplace", wid, permission=permission)


def _ctx(db, alice_id, sid, agent_id="main"):
    return db.access.resolve_context(alice_id, sid, agent_id)


# -- offline browser ----------------------------------------------------


@pytest.fixture
def real_container(world, monkeypatch):
    from app.runtime.isolation import tool_dispatch
    from app.runtime.isolation.backend import ContainerBackend

    db, admin, alice, sid = world
    runtime = os.environ.get("TOMO_SANDBOX_RUNTIME", "docker")
    image = os.environ.get("TOMO_SANDBOX_IMAGE", "tomo:sandbox")
    try:
        subprocess.run([runtime, "info"], capture_output=True, check=True, timeout=10)
        subprocess.run([runtime, "image", "inspect", image], capture_output=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("REAL container acceptance not verified: local runtime or image unavailable")
    broker = ContainerBackend(policy=db.access, runtime=runtime, image=image,
                              namespace="test-" + uuid.uuid4().hex)
    monkeypatch.setattr(tool_dispatch, "backend", broker)
    db.access.register_execution_stopper(broker.stop_session)
    fs = os.statvfs(db.get_workplace(db.get_session(sid)["workplace_id"])["root_path"])
    capacity_mb = (fs.f_blocks * fs.f_frsize // (1024 * 1024)) + 4096
    db.access.set_quota("usr_admin", alice["id"], {"disk_mb": capacity_mb, "memory_mb": 2048})
    yield broker, db, admin, alice, sid
    broker.close()


def test_member_offline_browser_python_workflow_in_container(real_container):
    """Real Python cloakbrowser launch() as a Member: title + text, no net."""
    from app.runtime.tools import browser as browser_tool

    _broker, db, _admin, alice, sid = real_container
    html = "<html><head><title>Member offline</title></head><body><p>offline-render-proof</p></body></html>"
    with execution_scope(_ctx(db, alice["id"], sid)):
        out = browser_tool.run({"html": html, "timeout": 60})
    assert "Member offline" in out, out
    assert "offline-render-proof" in out, out
    assert "subresources/websockets not executed live" in out


def test_member_offline_browser_owned_file_and_escape_denied(real_container):
    from app.runtime.tools import browser as browser_tool

    _broker, db, _admin, alice, sid = real_container
    ctx = _ctx(db, alice["id"], sid)
    path = db.get_workplace(ctx.active_workplace_id)["root_path"]
    with open(os.path.join(path, "page.html"), "w", encoding="utf-8") as fh:
        fh.write("<html><head><title>Owned file</title></head><body><p>file-proof</p></body></html>")
    with execution_scope(ctx):
        out = browser_tool.run({"file": "page.html", "timeout": 60})
        assert "Owned file" in out and "file-proof" in out, out
        # Container-absolute escape outside the owned root is refused.
        assert browser_tool.run({"file": "/etc/hostname"}).startswith("Error:")


def test_offline_render_fails_closed_without_backend(world, monkeypatch):
    """No container backend: fail closed, never host-execute, never fetch."""
    from app.runtime.browser import offline as offline_mod
    from app.runtime.isolation import tool_dispatch

    db, _admin, alice, sid = world
    monkeypatch.setattr(tool_dispatch, "backend", None)
    ctx = _ctx(db, alice["id"], sid)
    with pytest.raises(AccessUnavailable):
        offline_mod.render_offline_html(ctx, "<html><body><p>x</p></body></html>")


# -- online browser (scoped egress + per-hop SSRF) ------------------------


class _FixtureHandler(BaseHTTPRequestHandler):
    target_port = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/page":
            body = (
                "<html><head><title>Scoped page</title></head><body>"
                "<p>scoped-render-proof</p>"
                f"<img src=\"http://127.0.0.1:{self.target_port}/evil\">"
                "</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/redirect-out":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/unreachable")
            self.end_headers()
        elif self.path == "/redirect-in":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def fixture_origin(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = server.server_address[1]
    _FixtureHandler.target_port = port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("TOMO_BROWSER_TEST_ORIGIN", f"127.0.0.1:{port}")
    try:
        yield port
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_online_browser_policy_redirect_and_websocket(world, fixture_origin):
    """No container needed: URL shape, per-hop redirect, ws policy, fetch."""
    from app.runtime.browser import fetch as bfetch
    from app.services import store as _s

    db, _admin, alice, sid = world
    # Real scoped-egress setting through the settings seam.
    _s.update_settings({"network_egress": "scoped"})
    origin = f"http://127.0.0.1:{fixture_origin}"
    with execution_scope(_ctx(db, alice["id"], sid)):
        # Initial URL + in-allowance redirect hop validate.
        assert bfetch.check_browser_url(origin + "/page") is None
        assert bfetch.check_browser_url(origin + "/redirect-in") is None
        # Outside the exact allowance, loopback stays blocked (production SSRF).
        assert bfetch.check_browser_url("http://127.0.0.1:9/unreachable") is not None
        assert bfetch.check_browser_url("http://127.0.0.1:1/") is not None
        # Websocket schemes never open live sockets.
        assert bfetch.check_browser_url("ws://127.0.0.1:80/x") is not None
        # Redirect hop escaping the allowance denies instead of following.
        assert isinstance(bfetch.fetch_document(origin + "/redirect-out"), str)
        fetched = bfetch.fetch_document(origin + "/redirect-in")
        assert not isinstance(fetched, str), fetched
        body, final = fetched
        assert b"scoped-render-proof" in body and final.endswith("/page"), fetched


def test_online_browser_full_render_scoped(real_container, fixture_origin):
    """Real container render of a scoped-egress fetched page (Member)."""
    from app.runtime.tools import browser as browser_tool
    from app.services import store as _s

    _broker, db, _admin, alice, sid = real_container
    _s.update_settings({"network_egress": "scoped"})
    origin = f"http://127.0.0.1:{fixture_origin}"
    with execution_scope(_ctx(db, alice["id"], sid)):
        out = browser_tool.run({"url": origin + "/page", "timeout": 60})
    assert "Scoped page" in out and "scoped-render-proof" in out, out


def test_online_browser_denied_without_egress(world, fixture_origin):
    from app.runtime.browser import fetch as bfetch
    from app.runtime.tools import browser as browser_tool
    from app.services import store as _s

    db, _admin, alice, sid = world
    _s.update_settings({"network_egress": "off"})
    origin = f"http://127.0.0.1:{fixture_origin}"
    with execution_scope(_ctx(db, alice["id"], sid)):
        assert bfetch.check_browser_url(origin + "/page") is None  # URL shape ok
        out = browser_tool.run({"url": origin + "/page", "timeout": 20})
    assert out.startswith("Error:") and "disabled" in out, out


def test_production_ssrf_unchanged_by_test_allowance(world, monkeypatch):
    """Unsetting the test origin restores byte-identical production denial."""
    from app.runtime.browser import fetch as bfetch
    from app.runtime.tools.web_fetch import _check_url

    monkeypatch.delenv("TOMO_BROWSER_TEST_ORIGIN", raising=False)
    db, _admin, alice, sid = world
    with execution_scope(_ctx(db, alice["id"], sid)):
        assert bfetch.check_browser_url("http://127.0.0.1:8123/x") == _check_url("http://127.0.0.1:8123/x")
        assert bfetch.check_browser_url("http://127.0.0.1:8123/x") is not None
        assert bfetch.check_browser_url("ws://example.com/x") is not None


# -- restricted SSH contract ---------------------------------------------


def _ssh_world(world, tmp_path, marker="sha256:test-marker"):
    from app.workplaces import ssh_contract

    db, admin, alice, sid = world
    dest_root = tmp_path / "ssh-dest"
    dest_root.mkdir(exist_ok=True)
    (dest_root / ".tomo-sandbox-image").write_text(marker + "\n", encoding="utf-8")
    wid = _tag("ssh")
    db.create_workplace({"id": wid, "name": "SSH dest", "kind": "ssh",
                         "ssh_host": "test-transport", "ssh_user": "u",
                         "ssh_sandbox_root": str(dest_root),
                         "ssh_sandbox_image": marker})
    # Same convention as the tunnel suite: mark the probe status connected
    # (the probe itself needs a live host; contract behavior is what's
    # under test, over local destination subprocesses).
    db.with_db(lambda c: (c.execute(
        "UPDATE workplaces SET status='connected' WHERE id=?", (wid,)), c.commit()))
    wp = db.get_workplace(wid)
    assert wp["ssh_sandbox_root"] == str(dest_root)
    transport = ssh_contract.SubprocessTransport(dest_root, workplace_id=wid)
    return db, admin, alice, sid, wp, transport


def test_restricted_ssh_admit_exec_ro_escape_generation(world, tmp_path):
    from app.workplaces import ssh_contract

    db, admin, alice, sid, wp, transport = _ssh_world(world, tmp_path)
    try:
        _grant(db, admin, alice["id"], wp["id"])
        chat = db.create_swarm_session(["main"], user_id=alice["id"])
        db.access.set_chat_access(alice["id"], chat, wp["id"])
        ctx = _ctx(db, alice["id"], chat)
        att = ssh_contract.ensure_admitted(wp, ctx, transport)
        assert att["contract"] == 2 and att["sandbox"] and att["marker"] == "sha256:test-marker"
        # Status now reports a VERIFIED handshake, not the stored flag alone.
        assert ssh_contract.destination_status(wp)["verified"] is True
        out = ssh_contract.call_via_agent(wp, ctx, "exec_bash",
                                          {"script": "echo ssh-proof; pwd", "timeout": 10}, transport)
        assert out["ok"] and "ssh-proof" in out["result"]["stdout"], out
        assert str(tmp_path / "ssh-dest" / wp["id"]) in out["result"]["stdout"]
        # Read-write scope roundtrip.
        assert ssh_contract.call_via_agent(
            wp, ctx, "write_file", {"path": "note.txt", "content": "rw-proof"}, transport)["ok"]
        got = ssh_contract.call_via_agent(wp, ctx, "read_file", {"path": "note.txt"}, transport)
        assert got["ok"] and got["result"]["content"] == "rw-proof", got
        # Host-absolute escape refused by the destination agent.
        denied = ssh_contract.call_via_agent(wp, ctx, "read_file", {"path": "/etc/hostname"}, transport)
        assert not denied["ok"], denied
        # Confirmed teardown clears the live admission first: grant changes
        # below must not attempt a confirmed kill over real SSH in tests.
        ssh_contract.stop_session(chat, transport_factory=lambda _w: transport)
        assert ssh_contract.destination_status(wp)["verified"] is False
        # Read-only scope: writes refused, reads allowed (fresh session, so
        # no live admission exists when the grant changes).
        db.access.assign(admin["id"], alice["id"], "workplace", wp["id"], permission="read")
        chat_ro = db.create_swarm_session(["main"], user_id=alice["id"])
        db.access.set_chat_access(alice["id"], chat_ro, wp["id"])
        ctx_ro = _ctx(db, alice["id"], chat_ro)
        denied_w = ssh_contract.call_via_agent(
            wp, ctx_ro, "write_file", {"path": "nope.txt", "content": "x"}, transport)
        assert not denied_w["ok"], denied_w
        ok_r = ssh_contract.call_via_agent(wp, ctx_ro, "read_file", {"path": "note.txt"}, transport)
        assert ok_r["ok"] and ok_r["result"]["content"] == "rw-proof", ok_r
        # Leave chat_ro's admission live: the revoke below must attempt a
        # confirmed kill and retain the barrier when unreachable.
        # Revocation with an unreachable destination retains the barrier
        # (fail closed, same as tunnels): the production stopper cannot
        # confirm the kill over real SSH to a test-only destination.
        with pytest.raises(AccessUnavailable, match="(?i)teardown|blocked"):
            db.access.revoke(admin["id"], alice["id"], "workplace", wp["id"])
        with pytest.raises((AccessDenied, AccessUnavailable)):
            _ctx(db, alice["id"], chat)
        # The generation still bumped: a stale envelope fails closed at
        # the destination agent.
        stale_env = {"v": 1, "owner_user_id": alice["id"], "session_id": chat,
                      "agent_id": "main", "execution_mode": "restricted",
                      "destination_id": wp["id"], "active_workplace_id": wp["id"],
                      "access_generation": 0,
                      "resources": [{"workplace_id": wp["id"], "permission": "read_write",
                                      "destination_id": wp["id"], "kind": "ssh",
                                      "transfer_only": False}],
                      "quota": {"duration_seconds": 10}}
        stale = transport.call("exec", {"exec_context": stale_env, "script": "echo stale"},
                               timeout=10)
        assert not stale["ok"], stale
        # When the destination IS reachable (test transport), confirmed
        # teardown succeeds generation-tolerant and clears the badge.
        ssh_contract.stop_session(chat_ro, transport_factory=lambda _w: transport)
        assert ssh_contract.destination_status(wp)["verified"] is False
    finally:
        transport.close()
        ssh_contract.reset()


def test_unprepared_old_and_offline_ssh_reject(world, tmp_path):
    from app.runtime.tools import workplace_remote as wr
    from app.workplaces import ssh_contract

    db, admin, alice, sid, _wp, transport = _ssh_world(world, tmp_path)
    transport.close()
    try:
        # Unprepared (no provisioning): restricted capability denied, no transport.
        bare = db.create_workplace({"id": _tag("ssh"), "name": "Bare", "kind": "ssh",
                                    "ssh_host": "h", "ssh_user": "u"})
        db.with_db(lambda c: (c.execute(
            "UPDATE workplaces SET status='connected' WHERE id=?", (bare["id"],)), c.commit()))
        _grant(db, admin, alice["id"], bare["id"])
        chat = db.create_swarm_session(["main"], user_id=alice["id"])
        db.access.set_chat_access(alice["id"], chat, bare["id"])
        with execution_scope(_ctx(db, alice["id"], chat)):
            with pytest.raises(AccessUnavailable, match="(?i)restricted"):
                wr._require_remote_capability(db.get_workplace(bare["id"]), "restricted")
        # Marker mismatch: admitted nowhere, restricted refused at admission.
        other = db.create_workplace({"id": _tag("ssh"), "name": "Other", "kind": "ssh",
                                     "ssh_host": "t", "ssh_user": "u",
                                     "ssh_sandbox_root": str(tmp_path),
                                     "ssh_sandbox_image": "sha256:wrong"})
        db.with_db(lambda c: (c.execute(
            "UPDATE workplaces SET status='connected' WHERE id=?", (other["id"],)), c.commit()))
        _grant(db, admin, alice["id"], other["id"])
        chat2 = db.create_swarm_session(["main"], user_id=alice["id"])
        db.access.set_chat_access(alice["id"], chat2, other["id"])
        ctx2 = _ctx(db, alice["id"], chat2)
        t2 = ssh_contract.SubprocessTransport(tmp_path / "ssh-dest", workplace_id=other["id"])
        try:
            with pytest.raises(AccessUnavailable, match="(?i)marker|boundary"):
                ssh_contract.ensure_admitted(db.get_workplace(other["id"]), ctx2, t2)
        finally:
            t2.close()
        # Offline transport (dead subprocess): admission fails closed.
        dead = ssh_contract.SubprocessTransport(tmp_path / "ssh-dest", workplace_id="x")
        dead.close()
        with pytest.raises(AccessUnavailable, match="(?i)offline|unprepared|failed|answer"):
            ssh_contract.ensure_admitted(db.get_workplace(other["id"]), ctx2, dead)
        # Nothing verified from flags alone.
        assert ssh_contract.destination_status(db.get_workplace(other["id"]))["verified"] is False
    finally:
        ssh_contract.reset()


def test_unrestricted_ssh_keeps_grant_ack_caveat(world):
    """Unrestricted SSH still needs grant + acknowledgement (no agent)."""
    from app.runtime.tools import workplace_remote as wr

    db, admin, alice, sid = world
    wid = _tag("ssh")
    db.create_workplace({"id": wid, "name": "Raw", "kind": "ssh", "ssh_host": "h", "ssh_user": "u"})
    db.with_db(lambda c: (c.execute(
        "UPDATE workplaces SET status='connected' WHERE id=?", (wid,)), c.commit()))
    _grant(db, admin, alice["id"], wid)
    db.access.assign(admin["id"], alice["id"], "unrestricted", wid)
    # Capability gate passes for unrestricted without provisioning...
    wr._require_remote_capability(db.get_workplace(wid), "unrestricted")
    # ...but unrestricted activation still needs grant + acknowledgement:
    # without the ack, and after revoking the grant, activation denies.
    chat = db.create_swarm_session(["main"], user_id=alice["id"])
    db.access.set_chat_access(alice["id"], chat, wid)
    with pytest.raises(AccessDenied, match="(?i)acknowledgement"):
        db.access.set_chat_access(alice["id"], chat, wid,
                                  execution_mode="unrestricted",
                                  unrestricted_acknowledged=False)
    db.access.revoke(admin["id"], alice["id"], "unrestricted", wid)
    with pytest.raises(AccessDenied):
        db.access.set_chat_access(alice["id"], chat, wid,
                                  execution_mode="unrestricted",
                                  unrestricted_acknowledged=True)
