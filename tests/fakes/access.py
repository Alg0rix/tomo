"""Explicit owned local Admin contexts for legacy host-execution tests.

Policy, SQLite and supervision are real. This is not a restricted-container
fixture and does not grant privileges to anonymous identities or Members.

Helpers here always resolve through the real ``store.access`` policy for the
seeded ``usr_admin`` account with a destination-specific unrestricted grant
and explicit acknowledgement, mirroring the approved migration behaviour for
legacy Admin host chats. Tests for deliberately unavailable backends
(network/MCP/vision/portal/remote) must NOT use these helpers to fake
success; those stay denied until their service boundary exists.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ADMIN_USER_ID = "usr_admin"

_admin_login_counter = 0


def ensure_stoppers():
    """Register production teardown from sync or async tests alike."""
    import asyncio

    from app.main import _register_execution_stoppers

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        async def _register_once():
            _register_execution_stoppers()

        asyncio.run(_register_once())
    else:
        _register_execution_stoppers()


def owned_host_session(agent_ids=None, user_id=ADMIN_USER_ID):
    from app.services import store

    ensure_stoppers()
    sid = store.create_swarm_session(agent_ids or ['main'], user_id=user_id)
    wid = store.get_session(sid)['workplace_id']
    store.access.assign(ADMIN_USER_ID, user_id, 'unrestricted', wid)
    store.access.set_chat_access(user_id, sid, wid, execution_mode='unrestricted',
                                unrestricted_acknowledged=True)
    return sid


@contextmanager
def owned_admin_scope(agent_ids=None, user_id=ADMIN_USER_ID) -> Iterator[tuple]:
    """Bind an explicit owned unrestricted execution context.

    Defaults to the seeded Admin; pass an explicit ``user_id`` (with an Admin
    role for host execution) to scope another account. Yields
    ``(context, root)`` where ``root`` is the active workplace root that host
    file/shell tools are fenced to. Uses only real policy calls; no anonymous
    fallback and no Member elevation.
    """
    from app.runtime.access import execution_scope
    from app.services import store

    sid = owned_host_session(agent_ids, user_id=user_id)
    agent_id = (agent_ids or ['main'])[0]
    context = store.access.resolve_context(user_id, sid, agent_id)
    with execution_scope(context):
        yield context, Path(context.resources[0].root_path)


def admin_client(username=None, password="password123", role="admin"):
    """Real login as an explicit account; returns ``(client, user)``.

    Creates a fresh account (unique name per call) and logs in through the
    production ``POST /login`` + cookie session path. The middleware
    authenticates before route dependencies run, so ``dependency_overrides``
    alone cannot satisfy it; a fresh account also avoids depending on the
    ambient bootstrap-admin password. Each client gets a distinct peer name
    so the login rate limiter treats test logins as distinct peers.
    """
    global _admin_login_counter
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import store

    _admin_login_counter += 1
    username = username or f"testadmin{_admin_login_counter}"
    user = store.create_user(
        {"username": username, "password": password, "role": role})
    client = TestClient(
        app, base_url="https://testserver",
        client=(f"admintest-{_admin_login_counter}", 50000),
    )
    response = client.post(
        "/login", data={"username": username, "password": password})
    assert response.status_code in (200, 303), response.text[:200]
    me = client.get("/api/me")
    assert me.status_code == 200 and me.json()["id"] == user["id"], me.text[:200]
    return client, user


@contextmanager
def restricted_member_with_egress(
    username: str | None = None, password: str = "password123",
) -> Iterator[tuple]:
    """Explicit restricted Member + scoped egress for retrieval-tool tests.

    Real policy only: fresh Member account, assigned model + coordinator
    agent grants, personal-space chat, ``network_egress=scoped`` via the real
    settings path. Yields ``(context, session_id)``. Nothing anonymous, no
    privilege bypass — the SSRF/private-host guards stay on.
    """
    from app.runtime.access import execution_scope
    from app.services import store

    global _admin_login_counter
    _admin_login_counter += 1
    username = username or f"netmember{_admin_login_counter}"
    user = store.create_user({"username": username, "password": password, "role": "member"})
    profile = store.create_llm_profile({"name": f"Net{_admin_login_counter}", "model": "test-model", "api_key": "secret"})
    store.set_default_llm_profile(profile["id"])
    coord = store.get_coordinator()
    store.access.assign(ADMIN_USER_ID, user["id"], "model", profile["id"])
    store.access.assign(ADMIN_USER_ID, user["id"], "agent", coord["id"])
    # Assigned vision profile so auxiliary-vision checks pass honestly for
    # Members too (statically vision-capable model id, no network).
    vision = store.create_llm_profile(
        {"name": f"MemberVision{_admin_login_counter}", "model": "gpt-4o", "api_key": "secret"}
    )
    store.access.assign(ADMIN_USER_ID, user["id"], "model", vision["id"])
    # create_home_session → initialize_chat persists the member's only
    # visible (assigned) model on the session; no global default is touched.
    sid = store.create_home_session(user["id"])["session_id"]
    previous_egress = store.get_settings().get("network_egress", "off")
    store.update_settings({"network_egress": "scoped"})
    context = store.access.resolve_context(user["id"], sid)
    assert context.execution_mode == "restricted"
    try:
        with execution_scope(context):
            yield context, sid
    finally:
        # Files without their own rebind share the process DB: never leak
        # scoped egress into unrelated tests (default stays deny).
        try:
            store.update_settings({"network_egress": previous_egress})
        except Exception:
            pass

@contextmanager
def admin_with_vision_profile(
    username: str | None = None, password: str = "password123", egress: str | None = None,
) -> Iterator[tuple]:
    """Explicit owned Admin scope plus an assigned vision-capable profile.

    Real policy only: fresh Admin account, unrestricted grant on its personal
    chat (mirroring :func:`owned_admin_scope`), and an enabled ``gpt-4o``
    profile (statically vision-capable — no network) assigned to the
    account, so the auxiliary-vision assignment check passes honestly.
    Yields ``(context, root, profile)``.
    """
    from app.runtime.access import execution_scope
    from app.services import store

    global _admin_login_counter
    _admin_login_counter += 1
    username = username or f"visionadmin{_admin_login_counter}"
    user = store.create_user({"username": username, "password": password, "role": "admin"})
    sid = owned_host_session(["main"], user_id=user["id"])
    profile = store.create_llm_profile({"name": f"Vision{_admin_login_counter}", "model": "gpt-4o", "api_key": "secret"})
    store.access.assign(ADMIN_USER_ID, user["id"], "model", profile["id"])
    store.set_session_model(sid, profile["id"], profile["model"])
    from pathlib import Path

    context = store.access.resolve_context(user["id"], sid, "main")
    previous_egress = store.get_settings().get("network_egress", "off")
    if egress is not None:
        store.update_settings({"network_egress": egress})
    try:
        with execution_scope(context):
            yield context, Path(context.resources[0].root_path), profile
    finally:
        if egress is not None:
            try:
                store.update_settings({"network_egress": previous_egress})
            except Exception:
                pass
