"""Member tool execution: sandboxed MCP/plugin calls, scoped skills/state.

Covers the Member invocation boundary delivered in this stage:

* Member MCP ``stdio`` (Python) tools execute in the caller's own per-chat
  restricted container — never the shared host subprocess — with owner
  mounts (kernel RO), resource limits, duration enforcement,
  generation-gated revocation and no server-secret inheritance.
* Member ``streamable_http`` tools run as sanitized one-shot sessions
  (stored headers never inherited) behind scoped-egress + SSRF guards.
* Member plugin tools replay source-only in the same per-chat container;
  install/enable/discover stay Admin-only.
* ``use_skill`` reads are gated to the Member's assigned set with a
  per-user pin overlay (shared ``agent_skills`` untouched); ``agent_state``
  is namespaced per user.

Real temporary SQLite state, real Docker boundary (skipped with the same
explicit marker as the isolation suite when the image is unavailable),
real policy — no mocked auth/store/runtime.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from mcp import types as mcp_types

from app.runtime.access import AccessDenied, AccessUnavailable, execution_scope
from app.services import store

FIXTURE = Path(__file__).resolve().parents[1] / "fakes" / "mcp_member_server.py"
NODE_FIXTURE = Path(__file__).resolve().parents[1] / "fakes" / "mcp_member_node_server.js"
SECRET = "member-must-never-see-7f3a"

_counter = 0


def _next(prefix: str) -> str:
    global _counter
    _counter += 1
    return f"{prefix}{_counter}"


@pytest.fixture
def db(tmp_path):
    store.rebind(tmp_path / "member-tools.db")
    yield store


def _make_peer(username_prefix="mbrpeer"):
    from app.services import store as _store

    name = _next(username_prefix)
    peer = _store.create_user({"username": name, "password": "password123", "role": "member"})
    coord = _store.get_coordinator()
    profile = _store.list_llm_profiles()[0]
    _store.access.assign("usr_admin", peer["id"], "model", profile["id"])
    _store.access.assign("usr_admin", peer["id"], "agent", coord["id"])
    sid = _store.create_home_session(peer["id"])["session_id"]
    return {"user": peer, "agent": coord, "sid": sid}


@pytest.fixture
def member(tmp_path):
    del tmp_path
    from app.services import store as _store

    name = _next("mbr")
    user = _store.create_user({"username": name, "password": "password123", "role": "member"})
    profile = _store.create_llm_profile(
        {"name": f"P{name}", "model": "test-model", "api_key": "secret"})
    _store.set_default_llm_profile(profile["id"])
    coord = _store.get_coordinator()
    _store.access.assign("usr_admin", user["id"], "model", profile["id"])
    _store.access.assign("usr_admin", user["id"], "agent", coord["id"])
    sid = _store.create_home_session(user["id"])["session_id"]
    return {"user": user, "agent": coord, "sid": sid}


@pytest.fixture
def sandbox(monkeypatch):
    """Real per-chat container broker; skips explicitly without the image."""
    import importlib as _importlib

    # NOTE: ``app.runtime.isolation.__init__`` rebinds the ``backend``
    # attribute to the singleton, so resolve the real module object via
    # the module cache instead of attribute lookup.
    backend_module = _importlib.import_module("app.runtime.isolation.backend")

    runtime = os.environ.get("TOMO_SANDBOX_RUNTIME", "docker")
    image = os.environ.get("TOMO_SANDBOX_IMAGE", "tomo:sandbox")
    try:
        subprocess.run([runtime, "info"], capture_output=True, check=True, timeout=10)
        subprocess.run([runtime, "image", "inspect", image],
                       capture_output=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Member tool acceptance not verified: local runtime or tomo:sandbox image unavailable")
    from app.runtime.isolation.backend import ContainerBackend

    broker = ContainerBackend(policy=store.access, runtime=runtime, image=image,
                              namespace="test-" + uuid.uuid4().hex)
    monkeypatch.setattr(backend_module, "backend", broker)
    store.access.register_execution_stopper(broker.stop_session)
    try:
        yield broker
    finally:
        broker.close()
        try:
            store.access._stoppers.remove(broker.stop_session)
        except ValueError:
            pass


def _quota_for(user_id: str, session_id: str) -> None:
    wid = store.get_session(session_id)["workplace_id"]
    root = store.get_workplace(wid)["root_path"]
    fs = os.statvfs(root)
    capacity_mb = (fs.f_blocks * fs.f_frsize // (1024 * 1024)) + 4096
    store.access.set_quota("usr_admin", user_id,
                           {"disk_mb": capacity_mb, "memory_mb": 2048})


def _assign_tools(agent_id: str, *ids: str) -> None:
    assert store.set_agent_tools(agent_id, {tid: True for tid in ids}) is not None


async def _discover(server_id: str):
    from app.runtime.mcp import mcp_manager

    result = await mcp_manager.connect_and_discover(server_id)
    assert result["status"] == "connected", result
    return result


async def _close(server_id: str):
    from app.runtime.mcp import mcp_manager

    await mcp_manager.close_server(server_id)


def _make_stdio_server():
    # sys.executable on the host (Admin discovery); its basename (python)
    # is the Member-sandbox allowlisted interpreter inside the container.
    return store.create_mcp_server({
        "id": "msrv", "name": "Member Fixture", "transport": "stdio",
        "command": sys.executable, "args": [str(FIXTURE)],
        "env": {"MEMBER_MUST_NOT_SEE": SECRET},
    })


@pytest.mark.asyncio
async def test_member_mcp_stdio_executes_in_own_container(db, member, sandbox):
    _quota_for(member["user"]["id"], member["sid"])
    _make_stdio_server()
    await _discover("msrv")
    try:
        _assign_tools(member["agent"]["id"], "mcp__msrv__echo", "mcp__msrv__env_probe")
        from app.runtime.tools.registry import execute_async

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], member["agent"]["id"])
        with execution_scope(ctx):
            out = await execute_async("mcp__msrv__echo", {"text": "hi"})
            assert out == "echo: hi"
            env_keys = await execute_async("mcp__msrv__env_probe", {})
            assert "MEMBER_MUST_NOT_SEE" not in env_keys
            assert SECRET not in env_keys
            assert SECRET not in out
    finally:
        await _close("msrv")


@pytest.mark.asyncio
async def test_member_mcp_node_stdio_executes_in_own_container(db, member, sandbox):
    _quota_for(member["user"]["id"], member["sid"])
    store.create_mcp_server({
        "id": "nsrv", "name": "Member Node Fixture", "transport": "stdio",
        "command": "node", "args": [str(NODE_FIXTURE)],
    })
    await _discover("nsrv")
    try:
        _assign_tools(member["agent"]["id"], "mcp__nsrv__nodeecho")
        from app.runtime.tools.registry import execute_async

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], member["agent"]["id"])
        with execution_scope(ctx):
            out = await execute_async("mcp__nsrv__nodeecho", {"text": "hi"})
            assert out == "node-echo: hi"
    finally:
        await _close("nsrv")


@pytest.mark.asyncio
async def test_member_schema_offered_matches_callable_backend(db, member, sandbox):
    _quota_for(member["user"]["id"], member["sid"])
    _make_stdio_server()
    await _discover("msrv")
    try:
        _assign_tools(member["agent"]["id"], "mcp__msrv__echo")
        from app.runtime.mcp import mcp_manager
        from app.runtime.policy import filter_schemas, tool_available

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], member["agent"]["id"])
        live = mcp_manager.cached_ids_for_agent(member["agent"]["id"])
        assert "msrv" in live
        schemas = store.get_agent_openai_tools(member["agent"]["id"], connected_server_ids=live)
        names = {s.get("function", {}).get("name") for s in schemas}
        assert "mcp__msrv__echo" in names
        offered = filter_schemas(ctx, schemas)
        offered_names = {s.get("function", {}).get("name") for s in offered}
        assert "mcp__msrv__echo" in offered_names
        # Assigned but undiscovered/forged ids are never offered.
        assert not tool_available(ctx, "mcp__msrv__nope")
        assert "mcp__msrv__nope" not in offered_names
        # The offered tool is genuinely callable, not globally denied.
        from app.runtime.tools.registry import execute_async

        with execution_scope(ctx):
            assert await execute_async("mcp__msrv__echo", {"text": "live"}) == "echo: live"
    finally:
        await _close("msrv")


@pytest.mark.asyncio
async def test_member_cross_user_ceiling_denies(db, member, sandbox):
    _quota_for(member["user"]["id"], member["sid"])
    _make_stdio_server()
    await _discover("msrv")
    try:
        _assign_tools(member["agent"]["id"], "mcp__msrv__echo")
        other_agent = store.create_agent({"name": "OtherAgent"})
        store.access.assign("usr_admin", member["user"]["id"], "agent", other_agent["id"])
        from app.runtime.tools.registry import execute_async

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], other_agent["id"])
        with execution_scope(ctx):
            out = await execute_async("mcp__msrv__echo", {"text": "hi"})
            assert out.startswith("Error:")
        # Another account cannot bind this member's session at all.
        peer = store.create_user(
            {"username": _next("peer"), "password": "password123", "role": "member"})
        with pytest.raises((AccessDenied, AccessUnavailable)):
            store.access.resolve_context(peer["id"], member["sid"], other_agent["id"])
    finally:
        await _close("msrv")


@pytest.mark.asyncio
async def test_member_revocation_denies_after_grant_revoked(db, member, sandbox):
    _quota_for(member["user"]["id"], member["sid"])
    _make_stdio_server()
    await _discover("msrv")
    try:
        # A dedicated (non-coordinator) agent: revoking its grant removes
        # the member's only route to it (the shared coordinator stays
        # usable by design and is not revoked here).
        worker = store.create_agent({"name": "RevokeWorker"})
        store.access.assign("usr_admin", member["user"]["id"], "agent", worker["id"])
        _assign_tools(worker["id"], "mcp__msrv__echo")
        from app.runtime.tools.registry import execute_async

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], worker["id"])
        with execution_scope(ctx):
            assert await execute_async("mcp__msrv__echo", {"text": "before"}) == "echo: before"
            # Revoking the agent grant tears down the session (confirmed
            # container removal) and invalidates the bound ceiling.
            store.access.revoke("usr_admin", member["user"]["id"], "agent", worker["id"])
            out = await execute_async("mcp__msrv__echo", {"text": "after"})
            assert out.startswith("Error:")
        with pytest.raises((AccessDenied, AccessUnavailable)):
            store.access.resolve_context(
                member["user"]["id"], member["sid"], worker["id"])
    finally:
        await _close("msrv")


@pytest.mark.asyncio
async def test_member_ro_mount_refuses_rw_allows(db, member, sandbox):
    _quota_for(member["user"]["id"], member["sid"])
    _make_stdio_server()
    await _discover("msrv")
    try:
        _assign_tools(member["agent"]["id"], "mcp__msrv__write_probe")
        project = store.access.create_project("usr_admin", "SharedRef")
        store.access.share_project("usr_admin", project["id"], member["user"]["id"], "read")
        personal = store.get_session(member["sid"])["workplace_id"]
        store.access.set_chat_access(
            member["user"]["id"], member["sid"], personal,
            additional_workplace_ids=[project["id"]], execution_mode="restricted")
        from app.runtime.tools.registry import execute_async

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], member["agent"]["id"])
        ro_path = f"/workplaces/{project['id']}/probe.txt"
        rw_path = f"/workplaces/{personal}/probe.txt"
        with execution_scope(ctx):
            refused = await execute_async(
                "mcp__msrv__write_probe", {"path": ro_path, "content": "x"})
            assert refused.startswith("Error:")
            allowed = await execute_async(
                "mcp__msrv__write_probe", {"path": rw_path, "content": "hello"})
            assert allowed == "wrote 5 bytes"
    finally:
        await _close("msrv")


@pytest.mark.asyncio
async def test_member_duration_bound_terminates_tool(db, member, sandbox):
    _quota_for(member["user"]["id"], member["sid"])
    _make_stdio_server()
    await _discover("msrv")
    try:
        _assign_tools(member["agent"]["id"], "mcp__msrv__sleep_probe")
        import app.runtime.mcp.member_sandbox as member_sandbox

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], member["agent"]["id"])
        server = store.get_mcp_server("msrv", include_secrets=True)
        item = store.get_mcp_item_by_runtime_id("mcp__msrv__sleep_probe")
        with execution_scope(ctx):
            with pytest.raises(AccessUnavailable):
                await asyncio.to_thread(
                    member_sandbox.sandboxed_stdio_call,
                    ctx, server, item, {"seconds": 25}, timeout=3,
                )
    finally:
        await _close("msrv")


PLUGIN_SRC = """
def _add(args):
    return str(float(args.get("a", 0)) + float(args.get("b", 0)))

def _whereami(args):
    import json, os, socket
    return json.dumps({"hostname": socket.gethostname(), "uid": os.getuid()})

def setup(api):
    api.tool("add", "Add two numbers",
             {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
             _add)
    api.tool("whereami", "Environment report", {"type": "object"}, _whereami)
"""


@pytest.fixture
def member_plugin(tmp_path, monkeypatch):
    from app.plugins import manager as pm_module
    from app.plugins.manager import PluginManager

    src = tmp_path / "mtplug"
    src.mkdir()
    (src / "tomo-plugin.json").write_text(json.dumps(
        {"id": "membertools", "name": "MemberTools", "version": "1", "sdk_version": 1}))
    (src / "plugin.py").write_text(PLUGIN_SRC)
    pm = PluginManager(tmp_path / "phome")
    monkeypatch.setattr(pm_module, "_manager", pm)
    pm.install(str(src))
    pm.change("membertools", "enable")
    try:
        yield pm
    finally:
        pm.close()


@pytest.mark.asyncio
async def test_member_plugin_tool_executes_sandboxed(db, member, sandbox, member_plugin):
    _quota_for(member["user"]["id"], member["sid"])
    _assign_tools(member["agent"]["id"],
                  "plugin__membertools__add", "plugin__membertools__whereami")
    from app.runtime.tools.registry import execute as sync_execute
    from app.runtime.tools.registry import execute_async

    ctx = store.access.resolve_context(
        member["user"]["id"], member["sid"], member["agent"]["id"])
    with execution_scope(ctx):
        assert await execute_async("plugin__membertools__add", {"a": 2, "b": 3}) == "5.0"
        report = json.loads(sync_execute("plugin__membertools__whereami", {}))
        assert report["hostname"] != socket.gethostname()
        assert report["uid"] != 0
        assert SECRET not in json.dumps(report)
    # Disabled plugins fail closed for the same ceiling.
    member_plugin.change("membertools", "disable")
    with execution_scope(ctx):
        assert sync_execute("plugin__membertools__add", {"a": 1, "b": 1}).startswith("Error:")
        assert (await execute_async("plugin__membertools__add", {"a": 1, "b": 1})).startswith("Error:")


@pytest.mark.asyncio
async def test_member_unassigned_plugin_tool_denied(db, member, sandbox, member_plugin):
    _quota_for(member["user"]["id"], member["sid"])
    other_agent = store.create_agent({"name": "PluginOther"})
    store.access.assign("usr_admin", member["user"]["id"], "agent", other_agent["id"])
    from app.runtime.tools.registry import execute_async

    ctx = store.access.resolve_context(member["user"]["id"], member["sid"], other_agent["id"])
    with execution_scope(ctx):
        out = await execute_async("plugin__membertools__add", {"a": 1, "b": 2})
        assert out.startswith("Error:")


class _MemberFakeSession:
    def __init__(self, seen):
        self._seen = seen
        self.calls = []

    async def initialize(self):
        from types import SimpleNamespace

        return SimpleNamespace(serverInfo=SimpleNamespace(name="fake", version="1.0"),
                               capabilities=SimpleNamespace())

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=f"http-echo {arguments.get('text', '')}")])

    async def list_tools(self, cursor=None):
        return {"tools": [], "nextCursor": None}

    async def list_resources(self, cursor=None):
        return {"resources": [], "nextCursor": None}

    async def list_resource_templates(self, cursor=None):
        return {"resourceTemplates": [], "nextCursor": None}

    async def list_prompts(self, cursor=None):
        return {"prompts": [], "nextCursor": None}


def _seed_http_tool(server_id: str, url: str, headers: dict | None = None):
    server = store.create_mcp_server({
        "id": server_id, "name": server_id, "transport": "streamable_http", "url": url,
        **({"headers": headers} if headers else {}),
    })
    store.replace_mcp_items(server_id, [{
        "kind": "tool", "runtime_id": f"mcp__{server_id}__echo", "name": "echo",
        "title": "echo", "description": "Echo over HTTP", "uri": "", "mime_type": "",
        "schema": {"type": "function", "function": {
            "name": f"mcp__{server_id}__echo", "description": "Echo over HTTP",
            "parameters": {"type": "object"}}},
        "metadata": {"mcp_name": "echo", "server_id": server_id},
    }])
    return server


@pytest.mark.asyncio
async def test_member_http_strips_credentials_and_enforces_egress(db, member, monkeypatch):
    from app.runtime.mcp import mcp_manager

    _seed_http_tool("http1", "https://mcp.example/mcp",
                    headers={"Authorization": "Bearer " + SECRET})
    _assign_tools(member["agent"]["id"], "mcp__http1__echo")
    seen: dict = {}

    async def factory(server):
        from contextlib import AsyncExitStack as Stack

        seen["headers"] = dict(server.get("headers") or {})
        assert server.get("headers") == {}
        stack = Stack()
        session = _MemberFakeSession(seen)
        init = await session.initialize()
        return stack, session, init

    old_factory = mcp_manager.session_factory
    mcp_manager.session_factory = factory
    try:
        import app.runtime.tools.web_fetch as web_fetch

        monkeypatch.setattr(
            web_fetch, "_getaddrinfo",
            lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
        store.update_settings({"network_egress": "scoped"})
        from app.runtime.tools.registry import execute_async

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], member["agent"]["id"])
        with execution_scope(ctx):
            out = await execute_async("mcp__http1__echo", {"text": "over http"})
            assert out == "http-echo over http"
            assert seen["headers"] == {}
            assert SECRET not in out
    finally:
        mcp_manager.session_factory = old_factory
        store.update_settings({"network_egress": "off"})


@pytest.mark.asyncio
async def test_member_http_private_url_denied_even_with_egress(db, member):
    _seed_http_tool("http2", "http://127.0.0.1:9/mcp")
    _assign_tools(member["agent"]["id"], "mcp__http2__echo")
    store.update_settings({"network_egress": "scoped"})
    try:
        from app.runtime.tools.registry import execute_async

        ctx = store.access.resolve_context(
            member["user"]["id"], member["sid"], member["agent"]["id"])
        with execution_scope(ctx):
            out = await execute_async("mcp__http2__echo", {"text": "x"})
            assert out.startswith("Error:")
    finally:
        store.update_settings({"network_egress": "off"})


@pytest.mark.asyncio
async def test_member_http_egress_off_denied(db, member):
    _seed_http_tool("http3", "https://mcp.example/mcp")
    _assign_tools(member["agent"]["id"], "mcp__http3__echo")
    store.update_settings({"network_egress": "off"})
    from app.runtime.tools.registry import execute_async

    ctx = store.access.resolve_context(
        member["user"]["id"], member["sid"], member["agent"]["id"])
    with execution_scope(ctx):
        out = await execute_async("mcp__http3__echo", {"text": "x"})
        assert out.startswith("Error:")


@pytest.mark.asyncio
async def test_member_management_surfaces_stay_admin(db, member):
    from app.runtime.mcp import mcp_manager

    _make_stdio_server()
    ctx = store.access.resolve_context(
        member["user"]["id"], member["sid"], member["agent"]["id"])
    with execution_scope(ctx):
        with pytest.raises((AccessDenied, AccessUnavailable)):
            await mcp_manager.connect_and_discover("msrv")
        with pytest.raises((AccessDenied, AccessUnavailable)):
            await mcp_manager.read_resource("msrv", "test://x")
        with pytest.raises((AccessDenied, AccessUnavailable)):
            await mcp_manager.get_prompt("msrv", "p", {})
    await _close("msrv")


def _make_library_skill(skill_id: str, body: str) -> None:
    from app.extensions.skills import write_library_skill

    try:
        write_library_skill(skill_id=skill_id, name=skill_id, description=f"{skill_id} skill",
                            body=body)
    except FileExistsError:
        pass


@pytest.mark.asyncio
async def test_member_skills_scoped_read_and_pin(db, member):
    _make_library_skill("oat-milk", "Steam oat milk to 55C.")
    _make_library_skill("hidden-lore", "Nobody assigned reads this.")
    store.sync_skills()
    store.set_agent_skills(member["agent"]["id"], ["oat-milk"])
    _assign_tools(member["agent"]["id"], "use_skill", "list_skills")
    from app.runtime.agent.skills_prompt import build_skills_system_prompt
    from app.runtime.tools.registry import execute_async

    ctx = store.access.resolve_context(
        member["user"]["id"], member["sid"], member["agent"]["id"])
    with execution_scope(ctx):
        assert "55C" in await execute_async("use_skill", {"skill_id": "oat-milk"})
        denied = await execute_async("use_skill", {"skill_id": "hidden-lore"})
        assert denied.startswith("Error:")
        pinned = await execute_async(
            "use_skill", {"skill_id": "oat-milk", "activate": True})
        assert "55C" in pinned
        assert store.get_user_skill_activations(member["user"]["id"]) == ["oat-milk"]
    mine = build_skills_system_prompt(member["agent"]["id"], user_id=member["user"]["id"])
    assert "oat-milk*+" in mine
    admin_view = build_skills_system_prompt(member["agent"]["id"])
    assert "oat-milk+" not in admin_view
    assert "oat-milk*" in admin_view
    # Shared coordinator configuration is untouched by the pin.
    assigned = {s["id"] for s in store.get_agent_skills(member["agent"]["id"]) if s.get("assigned")}
    assert assigned == {"oat-milk"}
    peer = store.create_user(
        {"username": _next("skpeer"), "password": "password123", "role": "member"})
    theirs = build_skills_system_prompt(member["agent"]["id"], user_id=peer["id"])
    assert "oat-milk*+" not in theirs
    assert "oat-milk+" not in theirs


@pytest.mark.asyncio
async def test_member_skill_pin_cap_and_unpin(db, member, monkeypatch):
    _make_library_skill("oat-milk", "Steam oat milk to 55C.")
    store.sync_skills()
    store.set_agent_skills(member["agent"]["id"], ["oat-milk"])
    _assign_tools(member["agent"]["id"], "use_skill")
    monkeypatch.setattr(store, "MAX_USER_SKILL_ACTIVATIONS", 1)
    from app.runtime.tools.registry import execute_async

    ctx = store.access.resolve_context(
        member["user"]["id"], member["sid"], member["agent"]["id"])
    with execution_scope(ctx):
        assert "55C" in await execute_async(
            "use_skill", {"skill_id": "oat-milk", "activate": True})
        assert await execute_async(
            "use_skill", {"skill_id": "oat-milk", "activate": False}) != ""
        assert store.get_user_skill_activations(member["user"]["id"]) == []
    # A zero cap fails new pins closed without touching shared config.
    store.set_agent_skills(member["agent"]["id"], ["oat-milk"])
    monkeypatch.setattr(store, "MAX_USER_SKILL_ACTIVATIONS", 0)
    with execution_scope(ctx):
        capped = await execute_async(
            "use_skill", {"skill_id": "oat-milk", "activate": True})
        assert capped.startswith("Error:")
        assert store.get_user_skill_activations(member["user"]["id"]) == []


@pytest.mark.asyncio
async def test_member_agent_state_isolated_per_user(db, member):
    _assign_tools(member["agent"]["id"], "agent_state")
    from app.runtime.tools.registry import execute_async

    ctx = store.access.resolve_context(
        member["user"]["id"], member["sid"], member["agent"]["id"])
    with execution_scope(ctx):
        assert await execute_async(
            "agent_state", {"action": "set", "agent_id": member["agent"]["id"],
                            "key": "taste", "value": "oat"}) == (
            f"Saved agent state {member['agent']['id']}{CHR_USER}{member['user']['id']}.taste")
        assert await execute_async(
            "agent_state", {"action": "get", "agent_id": member["agent"]["id"],
                            "key": "taste"}) == "taste: oat"
    peer = _make_peer("stpeer")
    peer_ctx = store.access.resolve_context(peer["user"]["id"], peer["sid"], member["agent"]["id"])
    with execution_scope(peer_ctx):
        assert (await execute_async(
            "agent_state", {"action": "get", "agent_id": member["agent"]["id"],
                            "key": "taste"})).startswith("No state key")
    # No shared rows were created by the Member write.
    assert store.list_agent_state(member["agent"]["id"]) == {}
    # An agent the member may not use stays denied.
    other_agent = store.create_agent({"name": "StateOther"})
    with execution_scope(ctx):
        assert (await execute_async(
            "agent_state", {"action": "get", "agent_id": other_agent["id"],
                            "key": "taste"})).startswith("Error:")


CHR_USER = "::user::"


def test_member_stdio_rejects_non_interpreter_entrypoint(db):
    store.create_mcp_server({
        "id": "shsrv", "name": "Shell", "transport": "stdio",
        "command": "sh", "args": ["-c", "echo hi"],
    })
    server = store.get_mcp_server("shsrv", include_secrets=True)
    import app.runtime.mcp.member_sandbox as member_sandbox

    ok, reason = member_sandbox.sandboxable_stdio_server(server)
    assert not ok and "entrypoints" in reason


def test_member_stdio_rejects_missing_entrypoint_file(db):
    import app.runtime.mcp.member_sandbox as member_sandbox

    with pytest.raises(AccessDenied):
        member_sandbox._read_entrypoint_script(
            {"command": "python", "args": ["/nonexistent-entry-xyz.py"]})
