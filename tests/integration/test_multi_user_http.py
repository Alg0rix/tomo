"""Real app + SQLite HTTP perimeter; no external execution/services are mocked.

No managed work is started in these policy tests. Real startup teardown callbacks
are installed against empty chats; these tests do not claim OS isolation.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.services import store


@pytest.fixture
def http(tmp_path):
    store.rebind(tmp_path / "http-access.db")
    from app.main import _register_execution_stoppers, create_app

    app = create_app()
    admin = store.create_user({"username": "httpadmin", "password": "password123", "role": "admin"})
    alice = store.create_user({"username": "alice", "password": "password123"})
    bob = store.create_user({"username": "bob", "password": "password123"})
    profile = store.create_llm_profile({"name": "Assigned model", "model": "test-model", "api_key": "do-not-disclose-key"})
    store.set_default_llm_profile(profile["id"])
    for user in (alice, bob):
        store.access.assign(admin["id"], user["id"], "model", profile["id"])
    # Install production callbacks without starting Telegram/scheduler/network
    # supervisors. All managed-process registries are empty in this HTTP suite.
    async def register_stoppers():
        _register_execution_stoppers()

    asyncio.run(register_stoppers())

    def client(user):
        c = TestClient(app, base_url="https://testserver", follow_redirects=False,
                       client=(user["id"], 50000))
        assert c.post("/login", data={"username": user["username"], "password": "password123"}).status_code == 303
        return c

    clients = [client(user) for user in (admin, alice, bob)]
    yield app, admin, alice, bob, profile, *clients
    for c in clients:
        c.close()


def test_unowned_routines_are_never_visible_or_executable_even_for_admin(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    agent = store.access.list_visible_agents(admin['id'])[0]['id']
    job = store.create_schedule({'name': 'Unowned routine', 'agent_id': agent,
                                 'schedule': 'every 1h', 'message': 'Unowned private work'})
    assert job['id'] not in {s['id'] for s in ac.get('/api/schedules').json()['schedules']}
    page = ac.get('/scheduler')
    assert page.status_code == 200 and job['name'] not in page.text
    assert ac.post(f"/api/schedules/{job['id']}/run").status_code == 404
    for member in (c, bc):
        assert job['id'] not in {s['id'] for s in member.get('/api/schedules').json()['schedules']}
        assert job['name'] not in member.get('/scheduler').text
        assert member.get(f"/api/schedules/{job['id']}").status_code == 404


def test_manual_routine_http_run_propagates_authenticated_owner_to_real_runner_and_provider(http):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    app, admin, alice, bob, profile, ac, c, bc = http
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200)
            if body.get('stream'):
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                payload = {'choices': [{'index': 0, 'delta': {'content': 'Routine completed'},
                                        'finish_reason': 'stop'}]}
                self.wfile.write(('data: ' + json.dumps(payload) + '\n\ndata: [DONE]\n\n').encode())
            else:
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'choices': [{'message': {
                    'role': 'assistant', 'content': 'Routine completed'}, 'finish_reason': 'stop'}]}).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assigned = store.create_llm_profile({'name': 'Routine provider', 'model': 'routine-model',
            'api_key': 'local-test', 'base_url': f'http://127.0.0.1:{server.server_port}/v1'})
        store.set_default_llm_profile(assigned['id'])
        store.access.assign(admin['id'], alice['id'], 'model', assigned['id'])
        sid = store.create_home_session(alice['id'])['session_id']
        agent = store.get_session(sid)['coordinator_id']
        response = c.post('/api/schedules', json={'name': 'Owner routine', 'agent_id': agent, 'session_id': sid,
                         'schedule': 'every 1h', 'message': 'Run my routine'})
        assert response.status_code == 200, response.text
        job_id = response.json()['id']
        # Even Admin cannot manually run another account's private routine.
        assert ac.post(f'/api/schedules/{job_id}/run').status_code == 404
        assert bc.post(f'/api/schedules/{job_id}/run').status_code == 404
        run = c.post(f'/api/schedules/{job_id}/run')
        assert run.status_code == 200 and run.json()['status'] == 'ok', run.text
        run_sid = run.json()['session_id']
        assert store.get_session(run_sid)['user_id'] == alice['id']
        assert any(e['type'] == 'final' and 'Routine completed' in e['content']
                   for e in store.get_session_history(run_sid))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_member_cookie_and_key_cannot_reach_any_global_surface(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    key = c.post("/api/api-keys", json={"user_id": alice["id"], "name": "personal"})
    assert key.status_code == 200
    kc = TestClient(app, headers={"X-API-Key": key.json()["token"]}, follow_redirects=False)
    surfaces = [
        ("GET", "/api/users"), ("PUT", f"/api/users/{bob['id']}"),
        ("GET", "/api/settings"), ("PUT", "/api/settings"),
        ("GET", "/api/fs/browse?path=/"), ("POST", "/api/workplaces/ensure-local"),
        ("POST", "/api/workplaces"), ("GET", "/api/tools"),
        ("POST", "/api/skills/install"), ("POST", "/api/skills/sync"),
        ("POST", "/api/llm-profiles/provider-models"), ("GET", "/api/llm-profiles/codex-models"),
        ("POST", "/api/llm-profiles"), ("GET", "/api/mcp-servers"),
        ("GET", "/api/logs"), ("POST", "/api/update"), ("GET", "/system"),
        ("GET", "/evaluate"), ("GET", "/skills"), ("GET", "/agents"),
        ("GET", "/plugins/arbitrary/path"), ("POST", "/api/plugins/arbitrary/action"),
        ("PUT", f"/api/users/{alice['id']}/grants/unrestricted/nope"),
        ("POST", "/api/setup"), ("GET", "/setup"),
    ]
    for caller in (c, kc):
        for method, path in surfaces:
            response = caller.request(method, path, json={"path": "/", "enabled": True, "role": "admin"})
            assert response.status_code == 403, (method, path, response.text)
        assert caller.get("/api/me").json()["id"] == alice["id"]
    assert store.get_user(alice["id"])["role"] == "member"
    assert ac.get("/api/settings").status_code == 200
    assert ac.get("/api/fs/browse").status_code == 200
    assert ac.get("/api/users").status_code == 200
    assert ac.get("/api/users", headers={"Authorization": "Bearer tomo_bad"}).status_code == 401
    assert c.get("/api/dashboard/prompts").status_code == 200
    assert c.get("/static/js/chat.js").status_code == 200
    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.post("/api/setup", json={"setup_complete": True}).status_code == 401
    assert anonymous.get("/setup").status_code == 303
    assert anonymous.get("/static/js/chat.js").status_code == 200
    anonymous.close()
    kc.close()


def test_own_profile_keys_and_current_account_state(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    assert c.put("/api/me", json={"display_name": "Alice updated", "password": "new-password123"}).status_code == 200
    assert c.put("/api/me", json={"role": "admin"}).status_code == 422
    assert store.get_user(alice["id"])["role"] == "member"
    assert store.authenticate("alice", "new-password123") is not None
    key = c.post("/api/api-keys", json={"user_id": alice["id"]}).json()
    bob_key = bc.post("/api/api-keys", json={"user_id": bob["id"]}).json()
    assert c.get("/api/api-keys").json()["keys"][0]["id"] == key["id"]
    assert c.get("/api/api-keys", params={"user_id": bob["id"]}).status_code == 403
    assert c.post("/api/api-keys", json={"user_id": bob["id"]}).status_code == 403
    assert c.delete(f"/api/api-keys/{bob_key['id']}").status_code == 404
    kc = TestClient(app, headers={"Authorization": "Bearer " + key["token"]}, follow_redirects=False)
    assert ac.put(f"/api/users/{alice['id']}", json={"role": "admin"}).status_code == 200
    assert c.get("/api/users").status_code == kc.get("/api/users").status_code == 200
    assert ac.put(f"/api/users/{alice['id']}", json={"role": "member"}).status_code == 200
    assert c.get("/api/users").status_code == kc.get("/api/users").status_code == 403
    assert ac.put(f"/api/users/{alice['id']}", json={"enabled": False}).status_code == 200
    assert c.get("/api/me").status_code == kc.get("/api/me").status_code == 401
    assert c.get("/login").status_code == 200  # Disabled cookie cannot redirect-loop.
    kc.close()


def test_filtered_resources_and_non_disclosing_direct_ids(http, tmp_path):
    app, admin, alice, bob, profile, ac, c, bc = http
    external = store.create_workplace({"name": "Secret external folder", "kind": "local", "root_path": str(tmp_path)})
    tunnel = store.create_workplace({"name": "Hidden connector", "kind": "tunnel", "host": "hidden-host", "root_path": "/srv/private"})
    secret_agent = store.create_agent({"name": "Unassigned agent", "system_prompt": "secret-config"})
    secret_model = store.create_llm_profile({"name": "Unassigned model", "model": "private-model", "api_key": "hidden-secret"})
    key = store.access.create_personal_api_key(alice["id"])
    kc = TestClient(app, headers={"X-API-Key": key["token"]})
    for caller in (c, kc):
        wps = caller.get("/api/workplaces").json()["workplaces"]
        assert external["id"] not in {w["id"] for w in wps}
        assert not any("root_path" in w or "pairing_code" in w for w in wps)
        assert caller.get(f"/api/workplaces/{external['id']}").status_code == 404
        assert caller.get(f"/api/workplaces/{tunnel['id']}").status_code == 404
        assert caller.post(f"/api/workplaces/{tunnel['id']}/pairing-code").status_code == 403
        assert caller.get(f"/api/agents/{secret_agent['id']}").status_code == 404
        assert caller.get(f"/api/llm-profiles/{secret_model['id']}").status_code == 404
        assert "Unassigned" not in caller.get("/api/agents").text
        assert "hidden-secret" not in caller.get("/api/llm-profiles").text
        assert "do-not-disclose-key" not in caller.get(f"/api/llm-profiles/{profile['id']}").text
        assert caller.post("/v1/chat/completions", json={"model": secret_agent["id"], "messages": [{"role": "user", "content": "hello"}]}).status_code == 404
    assert ac.put(f"/api/users/{alice['id']}/grants/workplace/{external['id']}", json={"permission": "read"}).status_code == 200
    visible = c.get(f"/api/workplaces/{external['id']}").json()
    assert visible["permission"] == "read" and "root_path" not in visible
    kc.close()


def test_managed_sharing_chat_activation_and_explicit_unrestricted(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    sid = c.post("/api/sessions/home", json={}).json()["session_id"]
    active = c.get(f"/api/sessions/{sid}/access").json()["active_workplace_id"]
    project = bc.post("/api/projects", json={"name": "Reference"}).json()["workplace"]
    assert c.post("/api/projects", json={"name": "Escape", "root_path": "/"}).status_code == 422
    assert bc.post(f"/api/projects/{project['id']}/shares", json={"user_id": alice["id"], "permission": "read"}).status_code == 200
    assert c.post(f"/api/projects/{project['id']}/shares", json={"user_id": admin["id"], "permission": "read_write"}).status_code == 403
    scope = {"active_workplace_id": active, "additional_workplace_ids": [project["id"]]}
    assert c.put(f"/api/sessions/{sid}/access", json=scope).status_code == 200
    assert c.get(f"/api/sessions/{sid}/access").json()["additional_workplace_ids"] == [project["id"]]
    unrestricted = {**scope, "execution_mode": "unrestricted", "unrestricted_acknowledged": True}
    assert c.put(f"/api/sessions/{sid}/access", json=unrestricted).status_code == 403
    assert ac.put(f"/api/users/{alice['id']}/grants/unrestricted/{active}", json={"permission": "use"}).status_code == 200
    assert c.put(f"/api/sessions/{sid}/access", json={**unrestricted, "unrestricted_acknowledged": False}).status_code == 403
    assert c.put(f"/api/sessions/{sid}/access", json=unrestricted).status_code == 200
    assert bc.delete(f"/api/projects/{project['id']}/shares/{alice['id']}").status_code == 200
    assert c.get(f"/api/workplaces/{project['id']}").status_code == 404
    assert c.put(f"/api/sessions/{sid}/access", json=scope).status_code == 403


def test_sessions_attachments_artifacts_schedules_are_personal_even_for_admin(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    sid = c.post("/api/sessions/home", json={}).json()["session_id"]
    attachment = c.post(f"/api/sessions/{sid}/attachments", files={"file": ("note.txt", b"private-note", "text/plain")}).json()
    assert "file_path" not in attachment
    artifact = c.post(f"/api/sessions/{sid}/artifacts", json={"filename": "note.txt", "content": "private-artifact"})
    assert artifact.status_code == 200, artifact.text
    assert "filepath" not in artifact.json()
    assert c.get(f"/api/attachments/{attachment['id']}").content == b"private-note"
    assert c.get(f"/api/sessions/{sid}/artifacts/note.txt").text == "private-artifact"
    agent = store.get_coordinator()["id"]
    schedule = c.post("/api/schedules", json={"name": "Alice routine", "agent_id": agent, "session_id": sid, "schedule": "every 1h", "message": "private-routine"})
    assert schedule.status_code == 200, schedule.text
    schedule_id = schedule.json()["id"]
    assert "execution_context" not in schedule.json()
    for other in (bc, ac):
        for path in (f"/api/sessions/{sid}", f"/api/sessions/{sid}/chat", f"/api/attachments/{attachment['id']}",
                     f"/api/sessions/{sid}/artifacts/note.txt", f"/api/sessions/{sid}/pending", f"/api/sessions/{sid}/processes",
                     f"/api/schedules/{schedule_id}", f"/api/schedules/{schedule_id}/runs"):
            assert other.get(path).status_code == 404, path
        assert other.delete(f"/api/schedules/{schedule_id}").status_code == 404
        assert other.post(f"/api/schedules/{schedule_id}/run").status_code == 404
        assert other.post("/v1/chat/completions", headers={"X-Tomo-Session-Id": sid}, json={"model": agent, "messages": [{"role": "user", "content": "read private"}]}).status_code == 404
    assert "private-routine" not in bc.get("/api/home").text
    assert "Alice routine" not in bc.get("/scheduler").text
    assert c.delete(f"/api/attachments/{attachment['id']}").status_code == 200
    assert c.delete(f"/api/schedules/{schedule_id}").status_code == 200


def test_terminal_and_websocket_never_fallback_to_member_host(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    sid = c.post("/api/sessions/home", json={}).json()["session_id"]
    # No isolation-aware terminal method exists yet: fail before host spawning.
    from app.services.terminals import terminal_manager

    if not callable(getattr(terminal_manager, "create_for_context", None)):
        assert c.post(f"/api/sessions/{sid}/terminals", json={}).status_code == 503
        assert terminal_manager.list(sid, user_id=alice["id"]) == []
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect(f"wss://testserver/api/sessions/{sid}/terminals/nonexistent/ws", headers={"origin": "https://testserver"}):
            pytest.fail("Unknown host terminal was attached")
    assert exc.value.code in {4403, 4404}
    assert bc.post(f"/api/sessions/{sid}/terminals", json={}).status_code == 404


def test_attachment_turns_check_owner_and_refuse_privileged_preprocessing(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    sid = c.post("/api/sessions/home", json={}).json()["session_id"]
    other_sid = bc.post("/api/sessions/home", json={}).json()["session_id"]
    other = bc.post(f"/api/sessions/{other_sid}/attachments", files={"file": ("private.txt", b"bob-private", "text/plain")}).json()
    endpoint = f"/api/sessions/{sid}/chat/stream"
    assert c.post(endpoint, json={"message": "Read", "attachment_ids": [other["id"]]}).status_code == 404
    doc = c.post(f"/api/sessions/{sid}/attachments", files={"file": ("input.png", b"untrusted-image", "image/png")}).json()
    response = c.post(endpoint, json={"message": "Convert", "attachment_ids": [doc["id"]]})
    assert response.status_code == 503
    assert "preprocessing unavailable" in response.text
    assert c.get(f"/api/sessions/{sid}/chat").json()["entries"] == []


def test_artifact_symlink_cannot_expose_another_sessions_file(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    from app.runtime.artifacts.fs import artifacts_dir

    a_sid = c.post("/api/sessions/home", json={}).json()["session_id"]
    b_sid = bc.post("/api/sessions/home", json={}).json()["session_id"]
    assert bc.post(f"/api/sessions/{b_sid}/artifacts", json={"filename": "secret.txt", "content": "bob-private"}).status_code == 200
    base = artifacts_dir(a_sid)
    base.mkdir(parents=True, exist_ok=True)
    (base / "alias.txt").symlink_to(artifacts_dir(b_sid) / "secret.txt")
    for method in ("GET", "DELETE"):
        assert c.request(method, f"/api/sessions/{a_sid}/artifacts/alias.txt").status_code == 404
    assert c.post(f"/api/sessions/{a_sid}/artifacts/alias.txt/share").status_code == 404
    assert c.get(f"/api/sessions/{a_sid}/artifacts").status_code == 404
    assert bc.get(f"/api/sessions/{b_sid}/artifacts/secret.txt").text == "bob-private"


def test_owned_process_http_reads_and_controls_keep_identity_without_model(http):
    app, admin, alice, bob, profile, ac, c, bc = http
    sid = c.post('/api/sessions/home', json={}).json()['session_id']
    # Read/stop ownership does not require an executable assigned model.
    store.access.revoke(admin['id'], alice['id'], 'model', profile['id'])
    job = store.create_background_job({'session_id': sid, 'user_id': alice['id'],
                                       'status': 'running', 'stdout': 'completed output'})
    base = f'/api/sessions/{sid}/processes'
    assert [j['id'] for j in c.get(base).json()['jobs']] == [job['id']]
    assert c.get(base + '/' + job['id']).status_code == 200
    assert c.get(base + '/' + job['id'] + '/logs').json()['stdout'] == 'completed output'
    for caller in (ac, bc):
        assert caller.get(base).status_code == 404
        assert caller.post(base + '/' + job['id'] + '/stop').status_code == 404
    # No admitted OS handle exists: the real supervisor reports Unknown rather
    # than pretending the synthetic process was terminated.
    stopped = c.post(base + '/' + job['id'] + '/stop')
    assert stopped.status_code == 200 and stopped.json()['status'] == 'unknown'
    closed = c.post(base + '/' + job['id'] + '/close-monitoring')
    assert closed.status_code == 200 and closed.json()['monitoring_closed']


@pytest.mark.parametrize("credential", ["cookie", "api_key"])
def test_idor_private_object_reads_and_mutations(http, credential):
    """IDs in URLs, bodies and nested paths must not replace the caller's owner."""
    app, admin, alice, bob, profile, ac, c, bc = http
    caller = c
    if credential == "api_key":
        key = c.post("/api/api-keys", json={"user_id": alice["id"]}).json()
        caller = TestClient(app, headers={"Authorization": "Bearer " + key["token"]},
                            follow_redirects=False)
    try:
        sid = bc.post("/api/sessions/home", json={}).json()["session_id"]
        own_sid = c.post("/api/sessions/home", json={}).json()["session_id"]
        attachment = bc.post(f"/api/sessions/{sid}/attachments",
                             files={"file": ("private.txt", b"bob-idor-private", "text/plain")}).json()
        artifact_url = f"/api/sessions/{sid}/artifacts/private.txt"
        assert bc.post(f"/api/sessions/{sid}/artifacts",
                       json={"filename": "private.txt", "content": "bob-idor-private"}).status_code == 200
        episode = bc.post("/api/episodes", json={"title": "Bob private episode",
                                                "content": "bob-idor-private", "session_id": sid}).json()
        schedule = bc.post("/api/schedules", json={"name": "Bob private schedule",
                           "agent_id": store.get_coordinator()["id"], "session_id": sid,
                           "schedule": "every 1h", "message": "bob-idor-private"}).json()
        job = store.create_background_job({"session_id": sid, "user_id": bob["id"],
                                          "status": "succeeded", "stdout": "bob-idor-private"})
        scope = bc.get(f"/api/sessions/{sid}/access").json()
        private_paths = [
            f"/api/sessions/{sid}", f"/api/sessions/{sid}/chat", f"/api/sessions/{sid}/context",
            f"/api/sessions/{sid}/access", f"/api/sessions/{sid}/pending",
            f"/api/sessions/{sid}/attachments", f"/api/attachments/{attachment['id']}",
            artifact_url, artifact_url + "/share", f"/api/episodes/{episode['id']}",
            f"/api/schedules/{schedule['id']}", f"/api/schedules/{schedule['id']}/runs",
            f"/api/sessions/{sid}/processes/{job['id']}",
            # Changing the parent to an owned session must not grant the nested job.
            f"/api/sessions/{own_sid}/processes/{job['id']}",
            f"/api/sessions/{own_sid}/processes/{job['id']}/logs",
            f"/api/sessions/{sid}/terminals", f"/api/sessions/{sid}/secrets",
            f"/api/sessions/{sid}/connections",
        ]
        mutations = [
            ("DELETE", f"/api/sessions/{sid}", {}),
            ("PUT", f"/api/sessions/{sid}/access", {"active_workplace_id": scope["active_workplace_id"]}),
            ("POST", f"/api/sessions/{sid}/chat/clear", {}),
            ("POST", f"/api/sessions/{sid}/chat/stop", {}),
            ("PUT", f"/api/sessions/{sid}/approval-mode", {"mode": "off"}),
            ("DELETE", f"/api/attachments/{attachment['id']}", {}),
            ("POST", f"/api/attachments/{attachment['id']}/import", {}),
            ("DELETE", artifact_url, {}), ("POST", artifact_url + "/share", {}),
            ("DELETE", artifact_url + "/share", {}),
            ("POST", f"/api/episodes/{episode['id']}/feedback", {"helpful": False}),
            ("PUT", f"/api/schedules/{schedule['id']}", {"name": "Changed by Alice"}),
            ("DELETE", f"/api/schedules/{schedule['id']}", {}),
            ("POST", f"/api/schedules/{schedule['id']}/pause", {}),
            ("POST", f"/api/schedules/{schedule['id']}/resume", {}),
            ("POST", f"/api/schedules/{schedule['id']}/run", {}),
            ("POST", f"/api/sessions/{own_sid}/processes/{job['id']}/stop", {}),
            ("POST", f"/api/sessions/{own_sid}/processes/{job['id']}/close-monitoring", {}),
        ]
        for outsider in (caller, ac):
            for path in private_paths:
                response = outsider.get(path)
                assert response.status_code == 404, (credential, path, response.text)
                assert "bob-idor-private" not in response.text
            for method, path, payload in mutations:
                response = outsider.request(method, path, json={**payload, "user_id": bob["id"]})
                assert response.status_code == 404, (credential, method, path, response.text)
        # Owner reads prove denials did not corrupt/delete/reassign the target.
        assert bc.get(f"/api/sessions/{sid}").status_code == 200
        assert bc.get(f"/api/attachments/{attachment['id']}").content == b"bob-idor-private"
        assert bc.get(artifact_url).text == "bob-idor-private"
        assert bc.get(f"/api/schedules/{schedule['id']}").json()["name"] == "Bob private schedule"
        assert bc.get(f"/api/episodes/{episode['id']}").status_code == 200
        assert bc.get(f"/api/sessions/{sid}/processes/{job['id']}").json()["status"] == "succeeded"
        # Lists ignore forged owner selectors rather than disclose another account.
        for path in ("/api/sessions", "/api/episodes", "/api/schedules", "/api/companion", "/api/home"):
            response = caller.get(path, params={"user_id": bob["id"], "owner_id": bob["id"]})
            assert response.status_code == 200, (path, response.text)
            assert "bob-idor-private" not in response.text and "Bob private schedule" not in response.text
    finally:
        if caller is not c:
            caller.close()


@pytest.mark.parametrize("credential", ["cookie", "api_key"])
def test_idor_pending_requests_and_received_project_grants(http, credential):
    """Pending IDs derive ownership from server state, never submitted session IDs."""
    from app.runtime.permissions import hitl
    from app.services import secret_store

    app, admin, alice, bob, profile, ac, c, bc = http
    caller = c
    if credential == "api_key":
        key = c.post("/api/api-keys", json={"user_id": alice["id"]}).json()
        caller = TestClient(app, headers={"X-API-Key": key["token"]}, follow_redirects=False)
    sid = bc.post("/api/sessions/home", json={}).json()["session_id"]
    own_sid = c.post("/api/sessions/home", json={}).json()["session_id"]
    approval = hitl.create_approval(tool="write_file", args={"path": "private.txt"}, findings=[],
                                    description="Owner decision", session_id=sid)
    clarify = hitl.create_clarify(question="Owner question", session_id=sid)
    capability = secret_store.issue_capability(sid, bob["id"])
    pending = secret_store.create_request(capability, {"name": "private_bundle",
              "form": {"fields": [{"name": "secret", "type": "password"}]}})
    try:
        for outsider in (caller, ac):
            assert outsider.post(f"/api/approvals/{approval['id']}",
                                 json={"choice": "once", "session_id": own_sid}).status_code == 404
            assert outsider.post(f"/api/clarify/{clarify['id']}",
                                 json={"answer": "Not the owner", "session_id": own_sid}).status_code == 404
            # A valid owned parent does not permit resolving somebody else's request.
            for namespace in ("secrets", "connections"):
                response = outsider.post(f"/api/sessions/{own_sid}/{namespace}/requests/{pending['id']}",
                                         json={"values": {"secret": "not-a-real-secret"}, "user_id": bob["id"]})
                assert response.status_code == 404, response.text
        owner_pending = bc.get(f"/api/sessions/{sid}/pending").json()
        assert [p["id"] for p in owner_pending["approvals"]] == [approval["id"]]
        assert [p["id"] for p in owner_pending["clarifies"]] == [clarify["id"]]
        assert bc.post(f"/api/approvals/{approval['id']}", json={"choice": "deny"}).status_code == 200
        assert bc.post(f"/api/clarify/{clarify['id']}", json={"answer": "Owner answer"}).status_code == 200
        resolved = bc.post(f"/api/sessions/{sid}/secrets/requests/{pending['id']}",
                           json={"values": {"secret": "not-a-real-secret"}})
        assert resolved.status_code == 200, resolved.text
        bundle = resolved.json()["bundle"]
        assert caller.delete(f"/api/sessions/{own_sid}/secrets/{bundle['id']}").status_code == 404
        assert [b["id"] for b in bc.get(f"/api/sessions/{sid}/secrets").json()["bundles"]] == [bundle["id"]]
        # Even a read-write recipient may not administer or reshare the project.
        project = bc.post("/api/projects", json={"name": "Bob shared project"}).json()["workplace"]
        assert bc.post(f"/api/projects/{project['id']}/shares",
                       json={"user_id": alice["id"], "permission": "read_write"}).status_code == 200
        assert caller.get(f"/api/workplaces/{project['id']}").status_code == 200
        assert caller.get(f"/api/projects/{project['id']}/shares").status_code == 403
        assert caller.post(f"/api/projects/{project['id']}/shares",
                           json={"user_id": admin["id"], "permission": "read_write"}).status_code == 403
        assert caller.delete(f"/api/projects/{project['id']}/shares/{alice['id']}").status_code == 403
        assert bc.get(f"/api/projects/{project['id']}/shares").json()["shares"][0]["user_id"] == alice["id"]
    finally:
        hitl.cancel_session_pending(sid)
        hitl.clear_all_pending()
        secret_store.revoke_capability(capability)
        if caller is not c:
            caller.close()


def _admin_attachment_chat(admin, tmp_path):
    sid = store.create_home_session(admin['id'])['session_id']
    root = tmp_path / 'active'
    root.mkdir()
    wp = store.create_workplace({'name': 'Attachment destination', 'kind': 'local', 'root_path': str(root)})
    store.access.set_chat_access(admin['id'], sid, wp['id'], execution_mode='unrestricted')
    return sid, root


@pytest.mark.parametrize("vision_mode", ["native", "text"])
def test_admin_image_send_reaches_runtime_vision_and_import(http, tmp_path, vision_mode):
    """Real HTTP upload/send + runtime + owned bytes; only provider is local."""
    import base64
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    app, admin, alice, bob, profile, ac, c, bc = http
    requests = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            self.send_response(200)
            if body.get('stream'):
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                payload = {'choices': [{'index': 0, 'delta': {'content': 'Image received'}, 'finish_reason': 'stop'}]}
                self.wfile.write(('data: ' + json.dumps(payload) + '\n\ndata: [DONE]\n\n').encode())
            else:
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Image received'}, 'finish_reason': 'stop'}]}).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        model = store.create_llm_profile({'name': 'Native test vision', 'model': 'gpt-4o',
            'api_key': 'local-test', 'base_url': f'http://127.0.0.1:{server.server_port}/v1'})
        store.set_default_llm_profile(model['id'])
        store.update_settings({'image_input_mode': vision_mode, 'vision_profile_id': model['id'] if vision_mode == 'text' else ''})
        sid, root = _admin_attachment_chat(admin, tmp_path)
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
        att = ac.post(f'/api/sessions/{sid}/attachments', files={'file': ('pasted.png', png, 'image/png')}).json()
        sent = ac.post(f'/api/sessions/{sid}/chat/stream', json={'message': 'Describe', 'attachment_ids': [att['id']]})
        assert sent.status_code == 200, sent.text
        assert 'Image received' in sent.text, sent.text
        parts = [part for body in requests for msg in body.get('messages', [])
                 if isinstance(msg.get('content'), list) for part in msg['content']]
        image = next(p for p in parts if p.get('type') == 'image_url')
        from io import BytesIO
        from PIL import Image
        actual = Image.open(BytesIO(base64.b64decode(image['image_url']['url'].split(',', 1)[1])))
        original = Image.open(BytesIO(png))
        assert actual.size == original.size and actual.convert('RGBA').tobytes() == original.convert('RGBA').tobytes()
        imported = ac.post(f"/api/attachments/{att['id']}/import")
        assert imported.status_code == 200, imported.text
        path = Path(imported.json()['path'])
        assert path.parent == root / '.tomo-uploads'
        assert path.read_bytes() == png
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert ac.post(f"/api/attachments/{att['id']}/import").status_code == 403
        assert path.read_bytes() == png
        audit = [r for r in store.access.list_audit(admin['id']) if r['action'] == 'attachment.import']
        assert len(audit) == 1 and audit[0]['session_id'] == sid and audit[0]['actor_id'] == admin['id']
        other_sid = store.create_home_session(admin['id'])['session_id']
        assert ac.post(f'/api/sessions/{other_sid}/chat/stream', json={'message': 'Read', 'attachment_ids': [att['id']]}).status_code == 404
        assert c.post(f"/api/attachments/{att['id']}/import").status_code == 404
        binary = ac.post(f'/api/sessions/{sid}/attachments', files={'file': ('payload.bin', b'raw', 'application/octet-stream')}).json()
        assert ac.post(f'/api/sessions/{sid}/chat/stream', json={'message': 'Read', 'attachment_ids': [binary['id']]}).status_code == 503
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize('alias', ['folder', 'file', 'root', 'hardlink'])
def test_admin_import_refuses_destination_aliases(http, tmp_path, alias):
    from pathlib import Path
    app, admin, alice, bob, profile, ac, c, bc = http
    sid, root = _admin_attachment_chat(admin, tmp_path)
    att = ac.post(f'/api/sessions/{sid}/attachments', files={'file': ('note.txt', b'owned', 'text/plain')}).json()
    row = store.get_attachment(att['id'])
    outside = tmp_path / 'outside'
    outside.mkdir()
    target = outside / 'sentinel'
    target.write_bytes(b'preserve')
    folder = root / '.tomo-uploads'
    if alias == 'folder':
        folder.symlink_to(outside, target_is_directory=True)
    elif alias == 'root':
        root.rmdir()
        root.symlink_to(outside, target_is_directory=True)
    else:
        folder.mkdir()
        dest = folder / row['filename']
        if alias == 'file':
            dest.symlink_to(target)
        else:
            dest.hardlink_to(target)
    response = ac.post(f"/api/attachments/{att['id']}/import")
    assert response.status_code == 403, response.text
    assert target.read_bytes() == b'preserve'
    assert sorted(p.name for p in outside.iterdir()) == ['sentinel']
    assert not any(r['action'] == 'attachment.import' for r in store.access.list_audit(admin['id']))
    assert not list(Path(folder).glob('.import-*'))


def test_import_permissions_and_member_unrestricted_remain_denied(http, tmp_path):
    from dataclasses import replace
    from app.runtime.access import AccessDenied, execution_scope
    from app.runtime.isolation.attachments import import_upload

    app, admin, alice, bob, profile, ac, c, bc = http
    sid, root = _admin_attachment_chat(admin, tmp_path)
    att = ac.post(f'/api/sessions/{sid}/attachments', files={'file': ('note.txt', b'owned', 'text/plain')}).json()
    row = store.get_attachment(att['id'])
    context = store.access.resolve_context(admin['id'], sid)
    # A delegated read-only ceiling cannot be widened by the Admin role.
    readonly = replace(context, resources=tuple(replace(r, permission='read') for r in context.resources))
    with execution_scope(readonly), pytest.raises(AccessDenied, match='writable local'):
        import_upload(row)
    member_sid = store.create_home_session(alice['id'])['session_id']
    wid = store.get_session(member_sid)['workplace_id']
    store.access.assign(admin['id'], alice['id'], 'unrestricted', wid)
    store.access.set_chat_access(alice['id'], member_sid, wid, execution_mode='unrestricted', unrestricted_acknowledged=True)
    member_att = c.post(f'/api/sessions/{member_sid}/attachments', files={'file': ('note.txt', b'owned', 'text/plain')}).json()
    denied = c.post(f"/api/attachments/{member_att['id']}/import")
    assert denied.status_code == 503, denied.text
    image = c.post(f'/api/sessions/{member_sid}/attachments', files={'file': ('shot.png', b'fake-image', 'image/png')}).json()
    assert c.post(f'/api/sessions/{member_sid}/chat/stream', json={'message': 'Read', 'attachment_ids': [image['id']]}).status_code == 503
    # Even a valid local grant must have real OS write access.
    root.chmod(0o500)
    try:
        response = ac.post(f"/api/attachments/{att['id']}/import")
        assert response.status_code == 403, response.text
    finally:
        root.chmod(0o700)
    assert not (root / '.tomo-uploads').exists()


def test_auxiliary_vision_refuses_foreign_rows_and_upload_symlinks(http, tmp_path, monkeypatch):
    from app.runtime.access import execution_scope
    from app.services.vision import _describe_attachment

    app, admin, alice, bob, profile, ac, c, bc = http
    sid, root = _admin_attachment_chat(admin, tmp_path)
    other_sid = store.create_home_session(admin['id'])['session_id']
    other = ac.post(f'/api/sessions/{other_sid}/attachments', files={'file': ('secret.png', b'private', 'image/png')}).json()
    own = ac.post(f'/api/sessions/{sid}/attachments', files={'file': ('shot.png', b'owned', 'image/png')}).json()
    from pathlib import Path
    row = store.get_attachment(own['id'])
    path = Path(row['file_path'])
    path.unlink()
    path.symlink_to(store.get_attachment(other['id'])['file_path'])
    async def forbidden(*args, **kwargs):
        pytest.fail('Unauthorized bytes must never reach the provider')
    monkeypatch.setattr('app.runtime.llm.vision.analyze_image_data_url', forbidden)
    async def check():
        with execution_scope(store.access.resolve_context(admin['id'], sid)):
            for attachment in (row, store.get_attachment(other['id'])):
                assert await _describe_attachment(attachment, 'main', asyncio.Semaphore(1)) == (attachment['id'], '')
    asyncio.run(check())
    response = ac.post(f'/api/sessions/{sid}/chat/stream', json={'message': 'Read', 'attachment_ids': [own['id']]})
    assert response.status_code == 404
    assert ac.post(f"/api/attachments/{own['id']}/import").status_code == 403


def test_admin_restricted_image_and_nonlocal_import_are_not_enabled(http, tmp_path):
    app, admin, alice, bob, profile, ac, c, bc = http
    sid = store.create_home_session(admin['id'])['session_id']
    wp = store.access.ensure_personal_space(admin['id'])
    store.access.set_chat_access(admin['id'], sid, wp['id'], execution_mode='restricted')
    image = ac.post(f'/api/sessions/{sid}/attachments', files={'file': ('shot.png', b'image', 'image/png')}).json()
    response = ac.post(f'/api/sessions/{sid}/chat/stream', json={'message': 'Read', 'attachment_ids': [image['id']]})
    assert response.status_code == 503 and 'remove the image' in response.text
    # No remote connector is provisioned here; fail closed on the unavailable
    # destination rather than writing locally or falling back to host import.
    remote = store.create_workplace({'name': 'Remote attachment destination', 'kind': 'tunnel', 'root_path': '/remote/work'})
    store.access.set_chat_access(admin['id'], sid, remote['id'], execution_mode='unrestricted')
    assert ac.post(f"/api/attachments/{image['id']}/import").status_code == 503


@pytest.mark.parametrize('invalid_root', ['relative', 'parent', 'missing'])
def test_host_import_requires_an_existing_confined_absolute_root(tmp_path, invalid_root):
    from app.runtime.isolation.attachments import _import_local
    from app.runtime.access import AccessDenied
    root = {'relative': 'relative/work', 'parent': str(tmp_path / '..' / 'work'),
            'missing': str(tmp_path / 'missing')}[invalid_root]
    with pytest.raises((AccessDenied, OSError)):
        _import_local(root, 'att_safe.txt', b'owned')
    assert not (tmp_path / 'missing').exists()
