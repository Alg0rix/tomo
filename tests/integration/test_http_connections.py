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

import httpx2
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
    # Broker credential consumers require explicitly unrestricted local Admin
    # execution (Member/restricted consumers reject by design). The secure-form
    # and no-echo assertions below run in that owned Admin context.
    from tests.fakes.access import ensure_stoppers
    ensure_stoppers()
    user = store.create_user({"username": "alice", "password": "password1", "role": "admin"})
    from tests.fakes.access import owned_host_session
    sid = owned_host_session(["main"], user_id=user["id"])
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
    client = httpx2.Client(base_url=f"http://127.0.0.1:{port}")
    assert (
        client.post(
            "/login", data={"username": "alice", "password": "password1"}
        ).status_code
        == 303
    )
    # Direct CLI subprocess calls below go through require_host_execution:
    # bind the test Admin's own unrestricted context for the test duration.
    from tests.fakes.access import owned_admin_scope
    _cli_scope = owned_admin_scope(user_id=user["id"])
    _cli_scope.__enter__()
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
        _cli_scope.__exit__(None, None, None)
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
    with httpx2.Client(base_url=str(client.base_url)) as unauthenticated:
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
    # Same explicitly unrestricted treatment, so the cross-session lookup
    # itself is exercised (connection isolation → 404), not the exec gate.
    other_wid = store.get_session(other_sid)["workplace_id"]
    store.access.assign("usr_admin", uid, "unrestricted", other_wid)
    store.access.set_chat_access(uid, other_sid, other_wid, execution_mode="unrestricted",
                                 unrestricted_acknowledged=True)
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
    with httpx2.Client(base_url=str(client.base_url)) as bob_client:
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
        # No single-bundle GET route exists: unknown paths fail closed at
        # the perimeter instead of leaking bundle values.
        assert (
            client.get(
                "/api/secret-broker/bundles/workspace", headers=headers
            ).status_code
            == 401
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


def _private_file_bundle(client, sid, headers, values):
    pending = client.post(
        "/api/secret-broker/requests",
        headers=headers,
        json={
            "name": "deployment",
            "form": {
                "fields": [
                    {"name": key, "type": "textarea", "required": False}
                    for key in values
                ]
            },
        },
    ).json()
    response = client.post(
        f"/api/sessions/{sid}/secrets/requests/{pending['id']}", json={"values": values}
    )
    assert response.status_code == 200


def test_private_file_cli_preserves_config_and_round_trips_formats(
    environment, tmp_path, caplog
):
    import stat
    from dotenv import dotenv_values

    client, _, _, sid, uid, _ = environment
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / ".env"
    target.write_text(
        "# public settings\nAPP_PORT=3000\nexport ACCESS_KEY='old'\nACCESS_KEY='duplicate'\n",
        encoding="utf-8",
    )
    values = {
        "key": "synthetic $NEVER ${MISSING} $$ \\\\'single\" # é  ",
        "pem": "synthetic key\r\nsecond line\n",
        "blank": "",
    }
    token = secret_store.issue_capability(sid, uid, work_root=str(root))
    headers = {"Authorization": "Bearer " + token}
    _private_file_bundle(client, sid, headers, values)
    session_token = artifacts_fs.bind_session(sid)
    user_token = user_ctx.bind_user(uid)
    try:

        def apply(arguments):
            command = (
                "PYTHONPATH="
                + shlex.quote(str(ROOT))
                + " "
                + _command("secret apply deployment " + arguments)
            )
            result = bash._run_streaming(command, str(root), 30)
            assert result[0] == 0, result[2]
            assert values["key"] not in result[1] + result[2]
            assert values["pem"] not in result[1] + result[2]
            return json.loads(result[1])

        mapping = {"ACCESS_KEY": "key", "SIGNING_KEY": "pem", "OPTIONAL": "blank"}
        args = "--file .env --format dotenv --map " + shlex.quote(json.dumps(mapping))
        assert apply(args)["keys"] == list(mapping)
        content = target.read_bytes().decode()
        parsed = dotenv_values(target, interpolate=False)
        assert parsed == {
            "APP_PORT": "3000",
            **{k: values[v] for k, v in mapping.items()},
        }
        assert content.startswith("# public settings\nAPP_PORT=3000\n")
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        # Reapply/rotate without reading existing private values through the CLI.
        apply(args)
        assert target.read_bytes().decode() == content
        values["key"] += "-rotated"
        _private_file_bundle(client, sid, headers, values)
        apply(args)
        assert dotenv_values(target, interpolate=False) == {
            "APP_PORT": "3000",
            **{k: values[v] for k, v in mapping.items()},
        }
        (root / "config.json").write_text('{"port":3000,"nested":{"enabled":true}}')
        apply('--file config.json --format json --map \'{"credential":"key"}\'')
        assert json.loads((root / "config.json").read_text()) == {
            "port": 3000,
            "nested": {"enabled": True},
            "credential": values["key"],
        }
        apply("--file signing.pem --format text --field pem")
        assert (root / "signing.pem").read_bytes() == values["pem"].encode()
        # CLI resolves a changed working directory, rather than backend's cwd.
        (root / "app").mkdir()
        result = bash._run_streaming(
            "cd app && PYTHONPATH="
            + shlex.quote(str(ROOT))
            + " "
            + _command(
                "secret apply deployment --file .env --format compose --map "
                + shlex.quote(json.dumps(mapping))
            ),
            str(root),
            30,
        )
        assert result[0] == 0, result[2]
        assert values["key"] not in result[1] + result[2]
        assert (root / "app" / ".env").is_file()
        assert not list(root.rglob(".tomo-secret-*"))
        assert client.get(f"/api/sessions/{sid}/chat").json()["entries"] == []
        assert values["key"] not in caplog.text and values["pem"] not in caplog.text
        # The real Compose parser, when present, must preserve dollars/quotes/CRLF.
        import shutil
        import subprocess

        if (
            shutil.which("docker")
            and subprocess.run(
                ["docker", "compose", "version"], capture_output=True
            ).returncode
            == 0
        ):
            compose = root / "app" / "compose.yaml"
            compose.write_text(
                "services:\n  app:\n    image: busybox\n    env_file: .env\n"
                "    environment:\n      INJECTED: ${ACCESS_KEY}\n"
            )
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(root / "app" / ".env"),
                    "-f",
                    str(compose),
                    "config",
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert result.returncode == 0, result.stderr
            actual = json.loads(result.stdout)["services"]["app"]["environment"]
            # Compose's config serialization doubles literal dollars for replay.
            assert {k: v.replace("$$", "$") for k, v in actual.items()} == {
                **{k: values[v] for k, v in mapping.items()},
                "INJECTED": values["key"],
            }
    finally:
        artifacts_fs.reset_session(session_token)
        user_ctx.reset_user(user_token)
        secret_store.revoke_capability(token)


def test_private_file_application_errors_are_private_and_leave_files_unchanged(
    environment, tmp_path, caplog
):
    client, _, _, sid, uid, _ = environment
    root = tmp_path / "workspace"
    root.mkdir()
    token = secret_store.issue_capability(sid, uid, work_root=str(root))
    headers = {"Authorization": "Bearer " + token}
    _private_file_bundle(client, sid, headers, {"TOKEN": SECRET})
    endpoint = "/api/secret-broker/apply"
    data = {"bundle": "deployment", "file": ".env"}
    target = root / ".env"
    original = "TOKEN='" + SECRET + "\n"  # Invalid syntax containing a stored secret.
    target.write_text(original)
    result = client.post(endpoint, headers=headers, json=data)
    assert result.status_code == 400 and SECRET not in result.text
    assert target.read_text() == original and SECRET not in caplog.text
    # Failed mappings/formats/targets cannot partially write the existing file.
    for overrides in (
        {"mapping": {"TOKEN": "unknown"}},
        {"mapping": {"BAD-KEY": "TOKEN"}},
        {"format": []},
        {"format": "json"},
        {"file": "../outside.env"},
        {"file": "missing/.env"},
    ):
        result = client.post(endpoint, headers=headers, json={**data, **overrides})
        assert result.status_code == 400 and SECRET not in result.text
        assert target.read_text() == original
    link = root / "link.env"
    link.symlink_to(target)
    assert (
        client.post(
            endpoint, headers=headers, json={**data, "file": "link.env"}
        ).status_code
        == 400
    )
    assert target.read_text() == original
    other_sid = store.create_swarm_session(["main"], user_id=uid)
    # Same explicitly unrestricted treatment, so the cross-session bundle
    # lookup itself is exercised (missing bundle -> 404), not the exec gate.
    other_wid = store.get_session(other_sid)["workplace_id"]
    store.access.assign("usr_admin", uid, "unrestricted", other_wid)
    store.access.set_chat_access(uid, other_sid, other_wid, execution_mode="unrestricted",
                                 unrestricted_acknowledged=True)
    other = secret_store.issue_capability(other_sid, uid, work_root=str(root))
    result = client.post(
        endpoint, headers={"Authorization": "Bearer " + other}, json=data
    )
    assert result.status_code == 404 and SECRET not in result.text
    assert target.read_text() == original
    secret_store.revoke_capability(other)
    target.write_bytes(b"\xff" + SECRET.encode())
    result = client.post(endpoint, headers=headers, json=data)
    assert result.status_code == 400 and SECRET not in result.text
    assert target.read_bytes() == b"\xff" + SECRET.encode()
    target.write_text("APP_PORT=3000\n")
    _private_file_bundle(client, sid, headers, {"TOKEN": SECRET + "\x01"})
    result = client.post(endpoint, headers=headers, json=data)
    assert result.status_code == 400 and SECRET not in result.text
    assert target.read_text() == "APP_PORT=3000\n"
    secret_store.revoke_capability(token)
    assert client.post(endpoint, headers=headers, json=data).status_code == 401
    assert target.read_text() == "APP_PORT=3000\n" and not list(
        root.glob(".tomo-secret-*")
    )


@pytest.fixture()
def tunnel(environment, tmp_path):
    import os
    import shutil
    import subprocess
    from app.workplaces.hub import hub

    if not shutil.which("go"):
        pytest.skip("Real connector integration requires Go")
    binary = tmp_path / "tomo-connector"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/tomo-connector"],
        cwd=ROOT / "connector",
        check=True,
        timeout=120,
    )
    client, _, _, sid, uid, _ = environment
    wp = store.create_workplace(
        {"id": "wp_secrets", "name": "Secret tunnel", "kind": "tunnel"}
    )
    paired = store.pair_connector(wp["pairing_code"])
    home = tmp_path / "connector-home"
    root = tmp_path / "remote-work"
    home.mkdir()
    root.mkdir()
    (home / "state.json").write_text(
        json.dumps(
            {
                "server_url": str(client.base_url).rstrip("/"),
                "workplace_id": wp["id"],
                "token": paired["token"],
            }
        )
    )
    log = tmp_path / "connector.log"
    with log.open("wb") as output:
        proc = subprocess.Popen(
            [str(binary), "run"],
            env={
                **os.environ,
                "TOMO_CONNECTOR_HOME": str(home),
                "TOMO_CONNECTOR_ROOT": str(root),
            },
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 10
            while (
                not hub.is_online(wp["id"])
                and proc.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert hub.is_online(wp["id"]), log.read_text()
            assert hub.get(wp["id"]).secret_broker
            store.update_agent("main", {"workplace_id": wp["id"]})
            # Stage 4: the chat's active destination is the tunnel itself
            # (matching unrestricted grant + explicit acknowledgement, real
            # policy calls). Tool execution then routes through the verified
            # destination contract instead of the local host.
            store.access.assign(uid, uid, "unrestricted", wp["id"])
            store.access.set_chat_access(uid, sid, wp["id"], execution_mode="unrestricted",
                                         unrestricted_acknowledged=True)
            yield root, home, log, wp["id"]
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def test_real_tunnel_cli_forms_files_http_and_privacy(environment, tunnel):
    from dotenv import dotenv_values
    from app.runtime.tools import sandbox

    client, origin, upstream, sid, uid, _ = environment
    root, home, log, wid = tunnel
    # Destination-owned scope: the admitted workplace lands under
    # <connector-root>/<workplace_id>, never at coordinator paths.
    scope = root / wid
    scope.mkdir(parents=True, exist_ok=True)
    (scope / ".env").write_text("# public\nAPP_PORT=3000\n")
    values = {"KEY": SECRET, "PEM": "synthetic tunnel key\r\nsecond line\n"}
    session_token = artifacts_fs.bind_session(sid)
    user_token = user_ctx.bind_user(uid)
    agent_token = sandbox.bind_agent("main")
    from app.runtime.access import bind_execution as _bind_execution, reset_execution as _reset_execution
    tunnel_ctx = store.access.resolve_context(uid, sid)
    assert tunnel_ctx.active_workplace_id == wid
    exec_token = _bind_execution(tunnel_ctx)
    try:

        def remote(args, timeout=30):
            command = "PYTHONPATH=" + shlex.quote(str(ROOT)) + " " + _command(args)
            result = bash.run({"command": command, "timeout": timeout})
            assert SECRET not in result and values["PEM"] not in result
            return result

        def request(args, submitted):
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(copy_context().run, remote, args, 120)
                deadline = time.monotonic() + 10
                pending = []
                while not pending and time.monotonic() < deadline:
                    pending = (
                        client.get(f"/api/sessions/{sid}/pending")
                        .json()
                        .get("secrets", [])
                    )
                    time.sleep(0.05)
                assert len(pending) == 1
                saved = client.post(
                    f"/api/sessions/{sid}/secrets/requests/{pending[0]['id']}",
                    json=submitted,
                )
                assert saved.status_code == 200, saved.text
                assert SECRET not in saved.text
                result = future.result(timeout=15)
                assert json.loads(result.splitlines()[0])["status"] == "ready"

        form = {"fields": [{"name": "KEY"}, {"name": "PEM", "type": "textarea"}]}
        request(
            "secret request deployment --form " + shlex.quote(json.dumps(form)),
            {"values": values},
        )
        assert json.loads(remote("secret list"))["bundles"][0]["name"] == "deployment"
        assert json.loads(
            remote(
                'secret apply deployment --file .env --format dotenv --map \'{"API_KEY":"KEY"}\''
            )
        )["ok"]
        assert dotenv_values(scope / ".env", interpolate=False) == {
            "APP_PORT": "3000",
            "API_KEY": SECRET,
        }
        assert json.loads(
            remote("secret apply deployment --file key.pem --format text --field PEM")
        )["ok"]
        assert (scope / "key.pem").read_bytes() == values["PEM"].encode()
        (scope / "config.json").write_text('{"port":3000}')
        assert json.loads(
            remote(
                'secret apply deployment --file config.json --format json --map \'{"key":"KEY"}\''
            )
        )["ok"]
        assert json.loads((scope / "config.json").read_text()) == {
            "port": 3000,
            "key": SECRET,
        }
        (scope / "app").mkdir()
        assert json.loads(
            remote(
                'secret apply deployment --file app/.env --format compose --map \'{"TOKEN":"KEY"}\''
            )
        )["ok"]
        assert SECRET in (scope / "app" / ".env").read_text()
        unchanged = (scope / ".env").read_bytes()
        assert "exit code: 1" in remote("secret apply deployment --file ../outside.env")
        assert (scope / ".env").read_bytes() == unchanged
        http_form = {
            "fields": [{"name": "KEY"}],
            "auth": {"type": "bearer", "token_field": "KEY"},
        }
        request(
            "connection request gateway --url "
            + origin
            + " --form "
            + shlex.quote(json.dumps(http_form)),
            {"values": {"KEY": SECRET}, "allow_http": True},
        )
        assert len(json.loads(remote("connection list"))["connections"]) == 1
        # Saturate all ordinary RPC workers; consumers must have their own lane.
        with ThreadPoolExecutor(8) as pool:
            futures = [
                pool.submit(copy_context().run, remote, "http --connection gateway /")
                for _ in range(8)
            ]
            assert all(
                json.loads(f.result(timeout=40))["result"][0]["hostid"] == "42"
                for f in futures
            )
        assert all(r["auth"] == "Bearer " + SECRET for r in upstream)
        echoed = remote("http --connection gateway /reflect")
        assert "withheld" in echoed and "exit code: 22" in echoed
        before = len(upstream)
        assert "Redirect refused" in remote("http --connection gateway /redirect")
        assert len(upstream) == before + 1
        # Origin approval also pins execution location (localhost is not portable).
        local = secret_store.issue_capability(sid, uid)
        local_headers = {"Authorization": "Bearer " + local}
        pending = client.post(
            "/api/connection-broker/requests",
            headers=local_headers,
            json={"name": "backend-only", "base_url": origin, "auth_type": "bearer"},
        ).json()
        assert _approve(client, sid, pending).status_code == 200
        before = len(upstream)
        assert "different execution location" in remote(
            "http --connection backend-only /"
        )
        assert len(upstream) == before
        secret_store.revoke_capability(local)
        assert json.loads(remote("connection revoke backend-only"))["ok"]
        assert json.loads(remote("secret revoke deployment"))["ok"]
        assert json.loads(remote("connection revoke gateway"))["ok"]
        assert json.loads(remote("secret list"))["bundles"] == []
        assert "exit code: 1" in remote("http --connection gateway /")
        assert client.get(f"/api/sessions/{sid}/chat").json()["entries"] == []
        assert SECRET not in log.read_text() and values["PEM"] not in log.read_text()
        assert "method=secret_http" in log.read_text()
        # Persistent replay responses must not contain private reads/HTTP echoes.
        import base64

        for record in (home / "rpc-journal").rglob("*.json"):
            text = record.read_text()
            assert (
                SECRET not in text
                and base64.b64encode(SECRET.encode()).decode() not in text
            )
            response = json.loads(text).get("response", {}).get("result", {})
            if isinstance(response, dict):
                assert "content_b64" not in response and "body_b64" not in response
        assert not list(root.rglob(".tomo-secret-*"))
    finally:
        _reset_execution(exec_token)
        sandbox.reset_agent(agent_token)
        artifacts_fs.reset_session(session_token)
        user_ctx.reset_user(user_token)


def test_tunnel_broker_cross_session_expiry_and_cancel(environment, tunnel):
    from app.runtime.tools import sandbox

    client, _, _, sid, uid, _ = environment
    root, _, _, wid = tunnel
    token = secret_store.issue_capability(sid, uid, workplace_id=wid)
    headers = {"Authorization": "Bearer " + token}
    _private_file_bundle(client, sid, headers, {"TOKEN": SECRET})
    other_sid = store.create_swarm_session(["main"], user_id=uid)
    other = secret_store.issue_capability(other_sid, uid, workplace_id=wid)
    result = client.post(
        "/api/secret-broker/apply",
        headers={"Authorization": "Bearer " + other},
        json={"bundle": "deployment", "file": ".env"},
    )
    assert result.status_code == 404 and SECRET not in result.text
    assert not (root / ".env").exists()
    secret_store.revoke_capability(other)
    expired = secret_store.issue_capability(sid, uid, workplace_id=wid, ttl=1)
    time.sleep(1.05)
    assert (
        client.get(
            "/api/secret-broker/bundles", headers={"Authorization": "Bearer " + expired}
        ).status_code
        == 401
    )
    session_token = artifacts_fs.bind_session(sid)
    user_token = user_ctx.bind_user(uid)
    agent_token = sandbox.bind_agent("main")
    from app.runtime.access import bind_execution as _bind_execution2, reset_execution as _reset_execution2
    _exec_token2 = _bind_execution2(store.access.resolve_context(uid, sid))
    try:
        with ThreadPoolExecutor(1) as pool:
            command = (
                "PYTHONPATH="
                + shlex.quote(str(ROOT))
                + " "
                + _command(
                    'secret request cancelled --form \'{"fields":[{"name":"TOKEN"}]}\''
                )
            )
            future = pool.submit(
                copy_context().run, bash.run, {"command": command, "timeout": 120}
            )
            deadline = time.monotonic() + 10
            pending = []
            while not pending and time.monotonic() < deadline:
                pending = (
                    client.get(f"/api/sessions/{sid}/pending").json().get("secrets", [])
                )
                time.sleep(0.05)
            assert len(pending) == 1
            response = client.post(
                f"/api/sessions/{sid}/secrets/requests/{pending[0]['id']}",
                json={"cancel": True},
            )
            assert response.status_code == 200
            output = future.result(timeout=10)
            assert (
                "cancelled" in output
                and "exit code: 1" in output
                and SECRET not in output
            )
        assert not (root / ".env").exists()
    finally:
        _reset_execution2(_exec_token2)
        sandbox.reset_agent(agent_token)
        artifacts_fs.reset_session(session_token)
        user_ctx.reset_user(user_token)
