"""Real CLI → broker → local upstream, and the credential/authz boundary."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shlex
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn

from app.core import config
from app.core.secrets import encrypt_secret
from app.main import app
from app.runtime.artifacts import fs as artifacts_fs
from app.runtime.tools import bash, user_ctx
from app.services import secret_store, store

ROOT = Path(__file__).resolve().parents[2]
SECRET = "synthetic-zabbix-token-123456"


@pytest.fixture()
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TOMO_HOME", tmp_path)
    monkeypatch.delenv("TOMO_SECRET_KEY", raising=False)
    db = tmp_path / "connections.db"
    store.rebind(db)
    user = store.create_user({"username": "alice", "password": "password1"})
    sid = store.create_swarm_session(["main"], user_id=user["id"])
    requests = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.do_GET()

        def do_GET(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": body,
                }
            )
            self.send_response(302 if self.path == "/redirect" else 200)
            if self.path == "/redirect":
                self.send_header("Location", "/reflect")
            self.end_headers()
            result = (
                self.headers.get("Authorization", "")
                if self.path == "/reflect"
                else '{"result":[{"hostid":"42"}]}'
            )
            self.wfile.write(result.encode())

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    monkeypatch.setattr(config, "HOST", "127.0.0.1")
    monkeypatch.setattr(config, "PORT", port)
    server = uvicorn.Server(
        uvicorn.Config(app, lifespan="off", log_level="critical", access_log=False)
    )
    server_thread = threading.Thread(
        target=lambda: server.run(sockets=[sock]), daemon=True
    )
    server_thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}")
    assert (
        client.post(
            "/login", data={"username": "alice", "password": "password1"}
        ).status_code
        == 303
    )
    try:
        yield (
            client,
            f"http://127.0.0.1:{upstream.server_port}",
            requests,
            sid,
            user["id"],
            db,
        )
    finally:
        secret_store.cancel_session(sid)
        client.close()
        server.should_exit = True
        server_thread.join(5)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(2)
        sock.close()


def _command(args: str) -> str:
    return shlex.quote(sys.executable) + " -m cli " + args


def _approve(client, sid, pending, **overrides):
    return client.post(
        f"/api/sessions/{sid}/connections/requests/{pending['id']}",
        json={
            "secret": SECRET,
            "allow_http": True,
            **overrides,
        },
    )


def test_local_bash_cli_secure_form_and_http_round_trip(environment):
    client, origin, upstream, sid, uid, db = environment
    session_token = artifacts_fs.bind_session(sid)
    user_token = user_ctx.bind_user(uid)
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                copy_context().run,
                bash._run_streaming,
                _command(f"connection request zabbix --url {origin}"),
                str(ROOT),
                120,
            )
            deadline = time.monotonic() + 10
            pending = []
            while not pending and time.monotonic() < deadline:
                pending = (
                    client.get(f"/api/sessions/{sid}/pending").json().get("secrets", [])
                )
                time.sleep(0.05)
            assert len(pending) == 1
            assert "secret" not in pending[0] and "capability" not in pending[0]
            saved = _approve(client, sid, pending[0])
            assert saved.status_code == 200, saved.text
            assert SECRET not in saved.text
            code, stdout, stderr = future.result(timeout=10)
            assert code == 0, stderr
            assert json.loads(stdout)["connection"]["name"] == "zabbix"
            assert SECRET not in stdout + stderr
        # A fresh shell has a new capability, but can reuse this session's connection.
        payload = '{"jsonrpc":"2.0","method":"host.get","params":{},"id":1}'
        code, stdout, stderr = bash._run_streaming(
            _command(
                "http --connection zabbix -X POST /api_jsonrpc.php "
                "-H 'Content-Type: application/json' -d " + shlex.quote(payload)
            ),
            str(ROOT),
            30,
        )
        assert code == 0, stderr
        assert json.loads(stdout)["result"][0]["hostid"] == "42"
        assert upstream[-1]["auth"] == "Bearer " + SECRET
        assert SECRET not in stdout + stderr
        assert (
            SECRET.encode() not in db.read_bytes()
        )  # Ciphertext, not plaintext, at rest.
        listed = bash._run_streaming(_command("connection list"), str(ROOT), 30)
        assert SECRET not in listed[1] and "secret_ciphertext" not in listed[1]
        assert client.get(f"/api/sessions/{sid}/chat").json()["entries"] == []
        revoked = bash._run_streaming(
            _command("connection revoke zabbix"), str(ROOT), 30
        )
        assert revoked[0] == 0
        assert (
            client.get(f"/api/sessions/{sid}/connections").json()["connections"] == []
        )
    finally:
        artifacts_fs.reset_session(session_token)
        user_ctx.reset_user(user_token)


def test_broker_rejects_secret_echo_cross_session_and_origin_override(environment):
    client, origin, upstream, sid, uid, _ = environment
    token = secret_store.issue_capability(sid, uid)
    headers = {"Authorization": "Bearer " + token}
    pending = client.post(
        "/api/connection-broker/requests",
        headers=headers,
        json={
            "name": "zabbix",
            "base_url": origin,
            "auth_type": "bearer",
        },
    ).json()
    # The shell capability cannot supply or retrieve plaintext credentials.
    with httpx.Client(base_url=str(client.base_url)) as unauthenticated:
        denied = unauthenticated.post(
            f"/api/sessions/{sid}/connections/requests/{pending['id']}",
            headers=headers,
            json={"secret": SECRET},
        )
        assert denied.status_code == 401
    bad = _approve(client, sid, pending, secret=SECRET + "\n")
    assert bad.status_code == 400 and SECRET not in bad.text
    assert _approve(client, sid, pending).status_code == 200
    assert _approve(client, sid, pending).status_code == 409

    endpoint = "/api/connection-broker/http"
    echoed = client.post(
        endpoint, headers=headers, json={"connection": "zabbix", "path": "/reflect"}
    )
    assert echoed.json()["status_code"] == 502
    assert SECRET not in echoed.text
    before = len(upstream)
    redirect = client.post(
        endpoint, headers=headers, json={"connection": "zabbix", "path": "/redirect"}
    )
    assert redirect.json()["status_code"] == 302 and len(upstream) == before + 1
    assert (
        client.post(
            endpoint,
            headers=headers,
            json={
                "connection": "zabbix",
                "path": "//evil.example/",
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            endpoint,
            headers=headers,
            json={
                "connection": "zabbix",
                "path": "/",
                "headers": {"Host": "evil.example"},
            },
        ).status_code
        == 400
    )
    assert len(upstream) == before + 1

    other_sid = store.create_swarm_session(["main"], user_id=uid)
    other_token = secret_store.issue_capability(other_sid, uid)
    assert (
        client.post(
            endpoint,
            headers={"Authorization": "Bearer " + other_token},
            json={
                "connection": "zabbix",
                "path": "/",
            },
        ).status_code
        == 404
    )
    bob = store.create_user({"username": "bob", "password": "password1"})
    with httpx.Client(base_url=str(client.base_url)) as bob_client:
        bob_client.post("/login", data={"username": "bob", "password": "password1"})
        assert bob_client.get(f"/api/sessions/{sid}/connections").status_code == 404
        assert (
            bob_client.post(
                f"/api/sessions/{sid}/connections/requests/{pending['id']}",
                json={"secret": SECRET},
            ).status_code
            == 404
        )
    assert bob["id"] != uid
    # Legacy JSON-field auth is injected only by the broker, overwriting caller auth.
    pending = client.post(
        "/api/connection-broker/requests",
        headers=headers,
        json={
            "name": "zabbix",
            "base_url": origin,
            "auth_type": "json",
            "auth_field": "auth",
        },
    ).json()
    assert _approve(client, sid, pending).status_code == 200
    result = client.post(
        endpoint,
        headers=headers,
        json={
            "connection": "zabbix",
            "method": "POST",
            "path": "/api_jsonrpc.php",
            "body": '{"method":"host.get","auth":"caller-value"}',
        },
    )
    assert result.json()["status_code"] == 200
    assert json.loads(upstream[-1]["body"])["auth"] == SECRET
    assert SECRET not in result.text
    secret_store.revoke_capability(token)
    assert (
        client.post(
            endpoint, headers=headers, json={"connection": "zabbix", "path": "/"}
        ).status_code
        == 401
    )
    secret_store.revoke_capability(other_token)


def test_existing_connection_migrates_without_resurrecting_revoked_values(environment):
    client, origin, upstream, sid, uid, db = environment

    def old_install(conn):
        conn.execute(
            "INSERT INTO http_connections (id, session_id, user_id, name, base_url, auth_type, auth_field, secret_ciphertext, allow_http) VALUES (?,?,?,?,?,?,?,?,1)",
            (
                "conn_legacy",
                sid,
                uid,
                "legacy",
                origin,
                "bearer",
                "Authorization",
                encrypt_secret(SECRET),
            ),
        )
        conn.commit()

    store.with_db(old_install)
    store.rebind(db)
    token = secret_store.issue_capability(sid, uid)
    headers = {"Authorization": "Bearer " + token}
    result = client.post(
        "/api/connection-broker/http",
        headers=headers,
        json={"connection": "legacy", "path": "/"},
    )
    assert (
        result.json()["status_code"] == 200
        and upstream[-1]["auth"] == "Bearer " + SECRET
    )
    assert SECRET not in client.get("/api/secret-broker/bundles", headers=headers).text
    assert (
        client.delete("/api/secret-broker/bundles/legacy", headers=headers).status_code
        == 200
    )
    store.rebind(db)
    assert (
        client.get("/api/secret-broker/bundles", headers=headers).json()["bundles"]
        == []
    )
    secret_store.revoke_capability(token)


def test_dynamic_private_bundle_without_connection_or_protocol(environment):
    client, origin, upstream, sid, uid, db = environment
    definition = {
        "title": "Private workspace settings",
        "purpose": "Store operator-provided settings privately",
        "fields": [
            {"name": "ACCOUNT_NAME", "label": "Account", "type": "text"},
            {"name": "ACCESS_KEY", "label": "Access key", "type": "password"},
            {
                "name": "REGION",
                "label": "Region",
                "type": "select",
                "options": ["west", "east"],
            },
            {
                "name": "SIGNING_KEY",
                "label": "Signing key",
                "type": "textarea",
                "required": False,
            },
        ],
    }
    values = {
        "ACCOUNT_NAME": "synthetic-private-account",
        "ACCESS_KEY": "synthetic-dynamic-key",
        "REGION": "east",
        "SIGNING_KEY": "synthetic multiline\nprivate material\n",
    }
    session_token = artifacts_fs.bind_session(sid)
    user_token = user_ctx.bind_user(uid)
    try:
        with ThreadPoolExecutor(1) as pool:
            command = _command(
                "secret request workspace --form " + shlex.quote(json.dumps(definition))
            )
            future = pool.submit(
                copy_context().run, bash._run_streaming, command, str(ROOT), 120
            )
            deadline = time.monotonic() + 10
            pending = []
            while not pending and time.monotonic() < deadline:
                pending = (
                    client.get(f"/api/sessions/{sid}/pending").json().get("secrets", [])
                )
                time.sleep(0.05)
            assert len(pending) == 1 and pending[0]["usage"] == {}
            assert {f["name"] for f in pending[0]["form"]["fields"]} == set(values)
            saved = client.post(
                f"/api/sessions/{sid}/secrets/requests/{pending[0]['id']}",
                json={"values": values},
            )
            assert saved.status_code == 200, saved.text
            code, stdout, stderr = future.result(timeout=10)
            assert code == 0, stderr
            bundle = json.loads(stdout)["bundle"]
            assert bundle["usage"] == {} and "base_url" not in bundle
            assert "values" not in bundle and "values_ciphertext" not in bundle
            for key in ("ACCOUNT_NAME", "ACCESS_KEY", "SIGNING_KEY"):
                assert values[key] not in stdout + stderr + saved.text
                assert values[key].encode() not in db.read_bytes()
        token = secret_store.issue_capability(sid, uid)
        headers = {"Authorization": "Bearer " + token}
        # A trusted backend consumer can recover the exact map, including newlines.
        assert (
            secret_store.runtime_values(
                secret_store.scoped_bundle(
                    {"session_id": sid, "user_id": uid}, "workspace"
                )
            )
            == values
        )
        # Storage does not implicitly authorize HTTP (or any other execution).
        before = len(upstream)
        denied = client.post(
            "/api/connection-broker/http",
            headers=headers,
            json={"connection": "workspace", "path": "/"},
        )
        assert denied.status_code == 400 and len(upstream) == before
        assert (
            client.get(
                "/api/secret-broker/bundles/workspace", headers=headers
            ).status_code
            == 405
        )
        assert client.get(f"/api/sessions/{sid}/chat").json()["entries"] == []
        # The schema cannot smuggle input values/defaults into public metadata.
        invalid = {
            "name": "unsafe",
            "form": {
                "fields": [{"name": "TOKEN", "default": "synthetic-hidden-value"}]
            },
        }
        result = client.post(
            "/api/secret-broker/requests", headers=headers, json=invalid
        )
        assert result.status_code == 400 and "synthetic-hidden-value" not in result.text
        # HTTP is a separate consumer; its bindings can use arbitrary field names.
        form = {
            "fields": [{"name": "LOGIN_ID", "type": "text"}, {"name": "LOGIN_SECRET"}],
            "auth": {
                "type": "basic",
                "username_field": "LOGIN_ID",
                "password_field": "LOGIN_SECRET",
            },
        }
        pending = client.post(
            "/api/connection-broker/requests",
            headers=headers,
            json={"name": "custom", "base_url": origin, "form": form},
        ).json()
        response = _approve(
            client,
            sid,
            pending,
            values={
                "LOGIN_ID": values["ACCOUNT_NAME"],
                "LOGIN_SECRET": values["ACCESS_KEY"],
            },
        )
        assert response.status_code == 200, response.text
        result = client.post(
            "/api/connection-broker/http",
            headers=headers,
            json={"connection": "custom", "path": "/"},
        )
        assert result.json()["status_code"] == 200
        import base64

        assert (
            upstream[-1]["auth"]
            == "Basic "
            + base64.b64encode(
                (values["ACCOUNT_NAME"] + ":" + values["ACCESS_KEY"]).encode()
            ).decode()
        )
        assert (
            values["ACCOUNT_NAME"] not in result.text
            and values["ACCESS_KEY"] not in result.text
        )
        secret_store.revoke_capability(token)
    finally:
        artifacts_fs.reset_session(session_token)
        user_ctx.reset_user(user_token)
