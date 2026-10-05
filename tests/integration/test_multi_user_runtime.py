"""Runtime boundary evidence with real Store/contexts, not hidden tool menus.

LLM doubles represent an untrusted external model. Policy, SQLite, dispatcher,
private storage, PTY rejection and supervised host subprocesses are real.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core import config
from app.runtime.access import AccessDenied, execution_scope, current_execution
from app.runtime.agent.loop import run_turn
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.llm.mock import MockLLMClient
from app.runtime.tools.registry import execute, execute_async
from app.services.store import store


@pytest.fixture
def accounts(tmp_path):
    store.rebind(tmp_path / "runtime.db")
    alice = store.create_user({"username": "alice", "password": "password123"})["id"]
    bob = store.create_user({"username": "bob", "password": "password123"})["id"]
    profile = store.create_llm_profile({"name": "Assigned", "model": "test-model", "api_key": "secret"})
    store.set_default_llm_profile(profile["id"])
    for uid in (alice, bob):
        store.access.assign("usr_admin", uid, "model", profile["id"])
    sessions = [store.create_home_session(uid)["session_id"] for uid in (alice, bob)]
    contexts = [store.access.resolve_context(uid, sid) for uid, sid in zip((alice, bob), sessions)]
    store.set_agent_tools(contexts[0].agent_id, {name: True for name in (
        "bash", "memory", "session_search", "delegate", "schedule", "record_episode", "recall_episodes", "todo",
        "list_workplaces", "create_agent", "plugin_manager", "register_workplace", "agent_state",
    )})
    yield alice, bob, sessions, profile["id"]
    from app.services.background_jobs import manager
    manager.reset()
    store.rebind(Path(config.DB_PATH).parent / "after-runtime-tests.db")


def context(accounts, index=0):
    return store.access.resolve_context(accounts[index], accounts[2][index])


def test_root_install_admin_uses_host_without_sandbox_setup_but_member_cannot(accounts, monkeypatch, tmp_path):
    import os

    monkeypatch.setattr(os, "getuid", lambda: 0)
    sid = store.create_home_session("usr_admin")["session_id"]
    admin = store.access.resolve_context("usr_admin", sid)
    assert admin.execution_mode == "unrestricted"
    with execution_scope(admin):
        assert "admin-ready" in execute("bash", {"command": "test -d /tmp && printf admin-ready"})
    marker = tmp_path / "member-must-not-run-host"
    with execution_scope(context(accounts)):
        assert execute("bash", {"command": f"touch {marker}"}).startswith("Error:")
    assert not marker.exists()


def test_dispatch_requires_identity_and_approval_cannot_grant_platform_or_resources(accounts, tmp_path):
    from app.runtime.permissions.grants import set_outside_grant, reset_outside_grant
    marker = tmp_path / "privileged-host-executed"
    assert execute("bash", {"command": f"touch {marker}"}).startswith("Error:")
    assert not marker.exists()
    own = context(accounts)
    other = store.get_session(accounts[2][1])["workplace_id"]
    with execution_scope(own):
        token = set_outside_grant("*")
        try:
            for name in ("create_agent", "plugin_manager", "register_workplace", "agent_state", "use_skill"):
                assert execute(name, {"name": "Forged", "path": str(tmp_path)}).startswith("Error:")
            assert execute("bash", {"command": f"touch {marker}", "workplace": other}).startswith("Error:")
            assert execute("bash", {"command": f"touch {marker}", "workplace": "unknown-offline"}).startswith("Error:")
            assert execute("web_fetch", {"url": "http://127.0.0.1"}).startswith("Error:")
            assert execute("host-tool/" + "x" * 120, {}).startswith("Error:")
        finally:
            reset_outside_grant(token)
    assert not marker.exists()
    assert not any(a["name"] == "Forged" for a in store.list_agents())


def test_private_memory_episodes_sessions_and_artifacts_use_owner_not_shared_coordinator(accounts):
    from app.runtime.artifacts.fs import bind_session, reset_session
    alice, bob, sessions, _ = accounts
    store.append_session_history(sessions[0], {"type": "user", "content": "alice-private-needle"})
    with execution_scope(context(accounts)):
        assert "Saved" in execute("memory", {"action": "add", "entity": "user/profile", "content": "Alice loves private-needle"})
        assert "Recorded" in execute("record_episode", {"content": "Alice private-needle experience"})
        assert execute("record_episode", {"content": "Forged", "user_id": bob}).startswith("Error:")
        result = execute("save_artifact", {"filename": "private.txt", "content": "alice-private-needle"})
        assert json.loads(result)["session_id"] == sessions[0]
    with execution_scope(context(accounts, 1)):
        token = bind_session(sessions[1])
        try:
            assert "private-needle" not in execute("memory", {"action": "search", "query": "private-needle"})
            assert "alice-private-needle" not in execute("session_search", {"query": "private-needle"})
            assert "Alice" not in execute("recall_episodes", {"query": "private-needle"})
            assert execute("fetch_artifact", {"filename": "private.txt", "session_id": sessions[0]}).startswith("Error:")
        finally:
            reset_session(token)
    assert store.get_owned_session(sessions[0], bob) is None
    assert alice != bob


def test_catalogs_and_delegation_cannot_widen_parent_ceiling(accounts):
    from app.runtime.policy import authorized_agents
    from app.runtime.tools.delegate import bind_context, reset_context
    from app.runtime.agent.context import build_live_context
    peer = store.create_agent({"name": "Assigned peer"})
    secret = store.create_agent({"name": "Hidden-private-specialist"})
    store.access.assign("usr_admin", accounts[0], "agent", peer["id"])
    own = context(accounts)
    from app.models.mixins import swarm
    from app.runtime.tools import swarm_board
    other_run = store.with_db(lambda conn: swarm.create_run(conn, accounts[2][1], 'Bob private run'))
    with execution_scope(own):
        with pytest.raises(AccessDenied):
            swarm_board.bind(run_id=other_run, task_id='', agent_id=own.agent_id)
        visible = authorized_agents(own)
        assert peer["id"] in {a["id"] for a in visible}
        assert secret["id"] not in {a["id"] for a in visible}
        assert "Hidden-private-specialist" not in build_live_context(own.agent_id, session_id=own.session_id)
        token = bind_context(agent_ids=[peer["id"], secret["id"]], agents=[peer, secret])
        try:
            assert execute("delegate", {"agent_id": secret["id"]}).startswith("Error:")
            assert execute("delegate", {"agent_id": peer["id"]}) == f"Delegated to {peer['id']}"
        finally:
            reset_context(token)
        child = store.access.resolve_context(own.user_id, own.session_id, peer["id"], parent=own)
        assert child.resources == own.resources
        assert child.tool_ids <= own.tool_ids


@pytest.mark.asyncio
async def test_forced_model_tool_call_is_denied_before_approval_and_owner_is_bound(accounts):
    class HostileModel:
        calls = 0
        owners = []
        async def complete(self, messages, tools=None):
            self.owners.append(current_execution().user_id)
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content=None, tool_calls=[ToolCall(id="attack", name="create_agent", arguments={"name": "Attack"})])
            return LLMResponse(content="Denied safely")
    model = HostileModel()
    events = [e async for e in run_turn("create a shared agent", session_id=accounts[2][0], llm=model, enable_atg=False)]
    assert model.owners and set(model.owners) == {accounts[0]}
    assert not any(e['kind'] == 'approval_required' for e in events)
    assert any(e['kind'] == 'tool_result' and e.get('error') for e in events)
    assert not any(a['name'] == 'Attack' for a in store.list_agents())
    missing = [e async for e in run_turn("hello", session_id="missing", llm=MockLLMClient())]
    assert missing[0]['kind'] == 'error'
    assert current_execution(required=False) is None


def test_schedule_captures_owner_and_ceiling_and_other_account_cannot_manage(accounts):
    with execution_scope(context(accounts)):
        created = json.loads(execute("schedule", {"action": "create", "schedule": "every 1h", "message": "Summarize my work"}))
        assert created['success']
        sid = created['schedule_id']
    saved = store.get_schedule(sid)
    assert saved['owner_user_id'] == accounts[0]
    assert saved['execution_context']['session_id'] == accounts[2][0]
    with execution_scope(context(accounts, 1)):
        listed = json.loads(execute("schedule", {"action": "list", "include_disabled": True}))
        assert sid not in {j['id'] for j in listed['jobs']}
        assert not json.loads(execute("schedule", {"action": "remove", "schedule_id": sid}))['success']
    assert store.get_schedule(sid) is not None


def test_schedule_listing_is_account_wide_not_delivery_scoped(accounts):
    from app.channels.telegram import TelegramAPI
    from app.channels.telegram_context import bind_turn, reset_turn
    from app.channels.telegram_ui import TelegramTurnUI
    from app.runtime.artifacts.fs import bind_session, reset_session

    owner = context(accounts)
    local = store.access.create_schedule_for_context(owner, {
        'name': 'Local routine', 'schedule': 'every 1h', 'message': 'Local result'})
    telegram = store.access.create_schedule_for_context(owner, {
        'name': 'Telegram routine', 'schedule': 'every 1h', 'message': 'Telegram result',
        'delivery_target': {'version': 1, 'channel': 'telegram', 'chat_id': 42}})
    store.access.create_schedule_for_context(context(accounts, 1), {
        'name': 'Other account routine', 'schedule': 'every 1h', 'message': 'Private'})
    expected = {local['id'], telegram['id']}
    with execution_scope(owner):
        web = json.loads(execute('schedule', {'action': 'list'}))
        assert {job['id'] for job in web['jobs']} == expected
        # Listing must also work in a Telegram turn, without requiring that
        # the current delivery target matches a job or is authorized to send.
        # No network call is made by the real Telegram UI/API objects here.
        session_token = bind_session(owner.session_id)
        turn_token = bind_turn(TelegramTurnUI(TelegramAPI('not-used'), 42, owner.session_id))
        try:
            tg = json.loads(execute('schedule', {'action': 'list'}))
            assert {job['id'] for job in tg['jobs']} == expected
        finally:
            reset_turn(turn_token)
            reset_session(session_token)


@pytest.mark.asyncio
async def test_schedule_runs_selected_authorized_agent_in_owned_context_with_real_http_provider(accounts):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from app.scheduler.runner import fire_schedule
    models = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            models.append(request['model'])
            self.send_response(200)
            if request.get('stream'):
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                payload = {'id': 'test', 'choices': [{'index': 0, 'delta': {'content': 'Scheduled result'}, 'finish_reason': 'stop'}]}
                self.wfile.write(('data: ' + json.dumps(payload) + '\n\ndata: [DONE]\n\n').encode())
            else:
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Scheduled result'}, 'finish_reason': 'stop'}]}).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        profile = store.create_llm_profile({'name': 'Scheduled worker', 'model': 'worker-model',
            'api_key': 'local-test', 'base_url': f'http://127.0.0.1:{server.server_port}/v1'})
        peer = store.create_agent({'name': 'Scheduled specialist', 'model_id': profile['id']})
        store.access.assign('usr_admin', accounts[0], 'agent', peer['id'])
        store.access.assign('usr_admin', accounts[0], 'model', profile['id'])
        with execution_scope(context(accounts)):
            item = store.access.create_schedule_for_context(current_execution(), {'schedule': 'every 1h', 'message': 'Do my bounded work', 'agent_id': peer['id']})
        result = await asyncio.wait_for(fire_schedule(item, skip_claim=True), 15)
        assert result['status'] == 'ok', result
        assert models and set(models) == {'worker-model'}
        # Each fire runs in a fresh session owned by the schedule owner: the
        # final is visible there, never in the creation chat or another user.
        run_session = store.get_session(result['session_id'])
        assert run_session is not None and run_session['user_id'] == accounts[0]
        assert result['session_id'] not in (accounts[2][0], accounts[2][1])
        assert any(e.get('type') == 'final' and e.get('agent_id') == peer['id']
                   for e in store.get_session_history(result['session_id']))
        assert not store.get_session_history(accounts[2][1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.asyncio
async def test_revoked_schedule_and_external_services_fail_closed_without_replay(accounts):
    from app.scheduler.runner import fire_schedule
    with execution_scope(context(accounts)):
        item = store.access.create_schedule_for_context(current_execution(), {"schedule": "every 1h", "message": "Never replay", "agent_id": current_execution().agent_id})
        assert (await execute_async("mcp__private__read", {})).startswith("Error:")
    store.access.register_execution_stopper(lambda sid: None)  # No running execution in this test.
    store.access.revoke("usr_admin", accounts[0], "model", accounts[3])
    result = await fire_schedule(item, skip_claim=True)
    assert result['status'] == 'error'
    assert not any(m.get('content') == 'Never replay' for m in store.get_session_history(accounts[2][0]))


@pytest.mark.asyncio
async def test_member_unrestricted_terminal_requires_grant_and_keeps_role(accounts, tmp_path):
    # Stage 3 contract (spec: explicit unrestricted grant + chat activation;
    # platform role unchanged). Restricted Members get container terminals
    # through the HTTP API, never a host PTY here; a granted + activated
    # Member destination admits a supervised host PTY while staying Member.
    from app.services.terminals import TerminalManager
    manager = TerminalManager()
    own = context(accounts)
    assert own.role == "member"
    with execution_scope(own), pytest.raises(PermissionError):
        manager.create(own.session_id, tmp_path, 80, 24)
    store.access.register_execution_stopper(lambda sid: None)
    store.access.assign("usr_admin", own.user_id, "unrestricted", own.destination_id)
    store.access.set_chat_access(own.user_id, own.session_id, own.destination_id,
                               execution_mode="unrestricted", unrestricted_acknowledged=True)
    granted = context(accounts)
    assert granted.role == "member" and granted.execution_mode == "unrestricted"
    with execution_scope(granted):
        terminal = manager.create(granted.session_id, tmp_path, 80, 24)
    assert terminal.backend == "host"
    assert store.get_user(own.user_id)["role"] == "member"
    assert await manager.close(granted.session_id, terminal.id, user_id=own.user_id) is True
    await asyncio.to_thread(store.access.revoke, "usr_admin", own.user_id, "unrestricted", own.destination_id)
    # The chat still requests unrestricted mode, so even context resolution
    # now fails closed; no new host terminal can be admitted.
    with pytest.raises(AccessDenied):
        context(accounts)
    assert manager.terminals == {}
    with pytest.raises(AccessDenied):
        manager.list(accounts[2][1], user_id=accounts[0])


@pytest.mark.asyncio
async def test_revocation_cancels_and_drains_real_runtime_task_without_turn_limit(accounts):
    from app.runtime.supervision import stop_session
    entered = asyncio.Event()
    class WaitingProvider:
        async def complete(self, messages, tools=None):
            entered.set()
            await asyncio.Event().wait()
    store.access.register_execution_stopper(stop_session)
    async def drain(sid):
        return [e async for e in run_turn("wait", session_id=sid, llm=WaitingProvider(), enable_atg=False)]
    first = asyncio.create_task(drain(accounts[2][0]))
    await asyncio.wait_for(entered.wait(), 5)
    another = store.create_home_session(accounts[0])["session_id"]
    parallel = asyncio.create_task(drain(another))
    await asyncio.sleep(0.2)
    assert not parallel.done(), "a second chat of the same account must not be refused"
    parallel.cancel()
    await asyncio.gather(parallel, return_exceptions=True)
    await asyncio.to_thread(store.access.revoke, "usr_admin", accounts[0], "model", accounts[3])
    assert first.done()
    assert first.cancelled()
    with pytest.raises(AccessDenied):
        store.access.resolve_context(accounts[0], accounts[2][0])


def test_restricted_background_dispatch_never_falls_back_to_host(accounts, tmp_path):
    import time
    from app.services.background_jobs import manager
    marker = tmp_path / 'background-host-fallback'
    with pytest.raises(AccessDenied):
        manager.start(f'touch {marker}')
    own = context(accounts)
    with execution_scope(own):
        destination_name = store.get_workplace(own.active_workplace_id)['name']
        result = execute('bash', {'command': f'touch {marker}', 'background': True, 'workplace': destination_name})
        assert result.startswith('Started background job '), result
        job_id = result.splitlines()[0].split()[-1]
        job = manager.get_job(own.session_id, job_id)
        assert job['backend'] == 'container'
        assert job['user_id'] == own.user_id
        assert job['execution_context']['session_id'] == own.session_id
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = manager.get_job(own.session_id, job['id'])
            if job['status'] in {'failed', 'succeeded', 'stopped', 'interrupted'}:
                break
            time.sleep(.05)
        manager.stop_session(own.session_id)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_admin_matching_unrestricted_terminal_is_usable_and_teardown_is_confirmed(accounts):
    from app.services.terminals import TerminalManager
    manager = TerminalManager()
    sid = store.create_home_session('usr_admin')['session_id']
    resource = store.get_session(sid)['workplace_id']
    store.access.register_execution_stopper(manager.stop_session)
    store.access.assign('usr_admin', 'usr_admin', 'unrestricted', resource)
    store.access.set_chat_access('usr_admin', sid, resource, execution_mode='unrestricted', unrestricted_acknowledged=True)
    own = store.access.resolve_context('usr_admin', sid)
    with execution_scope(own):
        terminal = manager.create(sid, Path(own.resources[0].root_path), 80, 24)
        assert terminal.info()['running']
        await terminal.write('sleep 30 &\n')
    await asyncio.to_thread(store.access.set_quota, 'usr_admin', 'usr_admin', {'duration_seconds': 10})
    assert not terminal.info()['running']
    await manager.close_all()


def test_background_admin_host_execution_is_supervised_and_persists_explicit_owner(accounts):
    from app.runtime.artifacts.fs import bind_session, reset_session
    from app.runtime.tools.sandbox import bind_agent, reset_agent
    from app.services.background_jobs import manager
    from app.runtime.isolation import host
    sid = store.get_or_create_session(store.get_coordinator()['id'], 'usr_admin')
    resource = store.get_session(sid)['workplace_id']
    store.access.register_execution_stopper(lambda sid: None)
    store.access.assign('usr_admin', 'usr_admin', 'unrestricted', resource)
    store.access.set_chat_access('usr_admin', sid, resource, execution_mode='unrestricted', unrestricted_acknowledged=True)
    own = store.access.resolve_context('usr_admin', sid)
    with execution_scope(own):
        st, at = bind_session(sid), bind_agent(own.agent_id)
        try:
            job = manager.start("sleep 30")
            assert job['status'] == 'running'
            assert job['execution_context']['user_id'] == 'usr_admin'
            assert manager.get_job(sid, job['id'])['id'] == job['id']
        finally:
            reset_agent(at)
            reset_session(st)
    def stop(session_id):
        host.stop_session(session_id)
        manager.stop_session(session_id)
    store.access.register_execution_stopper(stop)
    store.access.set_quota('usr_admin', 'usr_admin', {'duration_seconds': 10})
    assert store.get_background_job(job['id'])['status'] in {'stopped', 'failed', 'interrupted'}
    with execution_scope(context(accounts)), pytest.raises(AccessDenied):
        manager.get_job(sid, job['id'])


def test_auxiliary_models_follow_owned_chat_and_never_use_unassigned_global_profile(accounts):
    from app.runtime.llm import get_auxiliary_llm, resolve_main_profile
    from app.runtime.policy import authorize_model_client
    sid = accounts[2][0]
    store.set_session_model(sid, accounts[3], 'test-model')
    hidden = store.create_llm_profile({'name': 'Private auxiliary', 'model': 'hidden-model', 'api_key': 'not-shared'})
    store.set_default_llm_profile(hidden['id'])
    captured = context(accounts)
    with execution_scope(captured):
        client = get_auxiliary_llm('learning_review')  # No explicit sid: inherit OWNER chat.
        assert client.context_profile['id'] == accounts[3]
        assert resolve_main_profile()['id'] == accounts[3]
        store.update_settings({'learning_review_profile_id': hidden['id']})
        with pytest.raises(AccessDenied):
            get_auxiliary_llm('learning_review')
        with pytest.raises(AccessDenied):
            get_auxiliary_llm('memory_extraction', session_id=accounts[2][1])
    store.access.register_execution_stopper(lambda sid: None)  # No managed work/client request.
    store.access.revoke('usr_admin', accounts[0], 'model', accounts[3])
    # A previously constructed client cannot continue sending private context.
    with execution_scope(captured), pytest.raises(AccessDenied):
        authorize_model_client(client)


@pytest.mark.asyncio
async def test_direct_service_calls_cannot_bypass_dispatch_or_private_owner(accounts):
    from app.runtime.tools import web_fetch, web_search, vision_analyze, portal, agent_info
    from app.runtime.memory.vault import extract, paths, write
    from app.runtime.mcp import mcp_manager
    from app.runtime.portal import transfers
    from app.runtime.tools import todo
    with execution_scope(context(accounts, 1)):
        assert 'Saved' in execute('memory', {'action': 'add', 'entity': 'project/private', 'content': 'Bob private fact'})
        bob_root = paths.vault_root(accounts[1])
    class Provider:
        calls = 0
        async def complete(self, *args, **kwargs):
            self.calls += 1
            return LLMResponse(content='[]')
    provider = Provider()
    with execution_scope(context(accounts)):
        for backend, args in (
            (web_fetch, {'url': 'http://127.0.0.1'}),
            (web_search, {'query': 'anything'}),
            (vision_analyze, {'source': 'attachment:private'}),
            (portal, {'action': 'list'}),
            (agent_info, {'action': 'list'}),
        ):
            with pytest.raises(AccessDenied):
                backend.run(args)
        # Member turns never spawn host MCP sessions: ensure returns the
        # cached-enabled subset (here: empty) without starting any
        # transport. Unassigned Member tool calls still deny at dispatch.
        assert await mcp_manager.ensure_for_servers({'private-server'}) == set()
        assert mcp_manager.connected_server_ids() == set()
        assert (await mcp_manager.call_tool(
            'mcp__private-server__echo', {})).startswith('Error:')
        with pytest.raises(AccessDenied):
            transfers.start_transfer('foreign:secret', 'own:copy', force_async=True)
        with pytest.raises(AccessDenied):
            paths.vault_root(accounts[1])
        with pytest.raises(AccessDenied):
            write.add_entity(accounts[1], 'project/private', 'Forged fact')
        with pytest.raises(AccessDenied):
            await extract.extract_turn(accounts[1], accounts[2][1], 'private data', '', provider)
        assert provider.calls == 0
        # Direct task-list use without the loop's older binding is still private.
        assert 'Alice task' in todo.run({'todos': [{'id': 'a', 'content': 'Alice task'}]})
    with execution_scope(context(accounts, 1)):
        assert 'Alice task' not in todo.run({})
        assert 'Forged fact' not in (bob_root / 'entities/project/private.md').read_text()
    records = store.access.list_audit('usr_admin', user_id=accounts[0])
    assert any(r['action'] == 'tool.web_fetch' and r['outcome'] == 'denied' for r in records)
    assert 'Bob private fact' not in json.dumps(records)


@pytest.mark.asyncio
async def test_manual_schedule_cannot_replace_bound_owner_and_malformed_durable_identity_denies(accounts):
    from app.scheduler.runner import run_schedule_now
    from app.runtime.policy import durable_context
    with execution_scope(context(accounts, 1)):
        scheduled = store.access.create_schedule_for_context(current_execution(), {'schedule': 'every 1h', 'message': 'Private Bob job'})
    with execution_scope(context(accounts)), pytest.raises(PermissionError):
        await run_schedule_now(scheduled['id'], user_id=accounts[1])
    with pytest.raises(AccessDenied):
        durable_context({'user_id': accounts[0], 'execution_context': {'user_id': accounts[0]}})
    assert not store.get_session_history(accounts[2][1])


def test_private_write_quota_includes_disabled_owned_projects(accounts):
    project = store.access.create_project(accounts[0], 'Stored but not active')
    root = Path(store.get_workplace(project['id'])['root_path'])
    (root / 'old-output.bin').write_bytes(b'x' * (1024 * 1024 + 1))
    store.access.register_execution_stopper(lambda sid: None)  # Only persisted files, no work.
    store.access.set_quota('usr_admin', accounts[0], {'disk_mb': 1})
    own = context(accounts)
    assert project['id'] not in {r.workplace_id for r in own.resources}
    with execution_scope(own):
        result = execute('save_artifact', {'filename': 'new.txt', 'content': 'must reject'})
        assert result.startswith('Error:') and 'quota' in result
        assert 'new.txt' not in execute('list_artifacts', {})


@pytest.mark.asyncio
async def test_actual_main_composite_stopper_drains_container_and_admitted_turn(accounts, monkeypatch):
    import os
    import uuid
    import subprocess
    from app.main import _register_execution_stoppers
    import app.runtime.isolation as isolation
    from app.runtime.isolation.backend import ContainerBackend
    from app.runtime.supervision import admitted_turn

    subprocess.run(['docker', 'image', 'inspect', 'tomo:sandbox'], check=True, capture_output=True, timeout=10)
    broker = ContainerBackend(policy=store.access, namespace='composite-' + uuid.uuid4().hex)
    monkeypatch.setattr(isolation, 'backend', broker)
    _register_execution_stoppers()
    own = context(accounts)
    root = Path(own.resources[0].root_path)
    fs = os.statvfs(root)
    await asyncio.to_thread(store.access.set_quota, 'usr_admin', own.user_id,
                            {'disk_mb': fs.f_blocks * fs.f_frsize // 1048576 + 4096, 'memory_mb': 2048})
    own = context(accounts)
    await asyncio.to_thread(broker.startup)
    async def running():
        with execution_scope(own):
            async with admitted_turn(own):
                return await asyncio.to_thread(broker.execute, own,
                    ['bash', '-lc', 'touch composite-started; sleep 30; touch composite-leaked'], timeout=40)
    task = asyncio.create_task(running())
    try:
        async with asyncio.timeout(10):
            while not (root / 'composite-started').exists():
                await asyncio.sleep(0.02)
        project = store.access.create_project(own.user_id, 'Next active folder')
        await asyncio.to_thread(store.access.set_chat_access, own.user_id, own.session_id, project['id'])
        await asyncio.gather(task, return_exceptions=True)
        assert task.done() and not (root / 'composite-leaked').exists()
        assert not store.get_session(own.session_id)['access_pending']
        with pytest.raises(AccessDenied):
            broker.execute(own, ['true'])
        assert not subprocess.run(['docker', 'ps', '-q', '--filter',
            'label=org.tomo.sandbox.namespace=' + broker.namespace], capture_output=True, text=True, check=True).stdout.strip()
        # Even an unavailable container runtime must not short-circuit cleanup
        # of known host work. Failure retains the persistent policy barrier.
        from app.runtime.isolation import host
        from app.runtime.access import AccessUnavailable
        admin_sid = store.create_home_session('usr_admin')['session_id']
        active = store.get_session(admin_sid)['workplace_id']
        await asyncio.to_thread(store.access.assign, 'usr_admin', 'usr_admin', 'unrestricted', active)
        await asyncio.to_thread(store.access.set_chat_access, 'usr_admin', admin_sid, active,
                                execution_mode='unrestricted', unrestricted_acknowledged=True)
        with execution_scope(store.access.resolve_context('usr_admin', admin_sid)):
            child = host.popen(['sleep', '30'])
        broken = ContainerBackend(policy=store.access, runtime='/missing-runtime', namespace='unavailable-' + uuid.uuid4().hex)
        monkeypatch.setattr(isolation, 'backend', broken)
        _register_execution_stoppers()
        with pytest.raises(AccessUnavailable):
            await asyncio.to_thread(store.access.set_chat_access, 'usr_admin', admin_sid, active)
        assert child.poll() is not None
        assert store.get_session(admin_sid)['access_pending']
        # Repair the SAME registered backend; appending a new stopper cannot
        # waive the old backend's unconfirmed cleanup.
        broken.runtime = 'docker'
        await asyncio.to_thread(store.access.set_chat_access, 'usr_admin', admin_sid, active)
        assert not store.get_session(admin_sid)['access_pending']
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(broker.close)
