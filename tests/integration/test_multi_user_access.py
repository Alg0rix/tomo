"""Real SQLite authorization seam, including current credentials and revocation.

These tests prove foundation policy and supervised teardown contracts, not
container/remote isolation; those require the isolation worker's real backend.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.core.deps import authenticated_user, require_admin, require_auth
from app.models.db import get_connection
from app.models.schema import _SCHEMA, migrate
from app.models.mixins import users
from app.runtime.access import (
    AccessDenied, AccessUnavailable, ExecutionContext, current_execution, execution_scope,
)
from app.services.store import Store, store


@pytest.fixture
def db(tmp_path):
    instance = Store(tmp_path / "authz.db")
    yield instance
    instance._conn.close()


def member(db, username):
    return db.create_user({"username": username, "password": "password123"})["id"]


def model_for(db, *uids):
    profile = db.create_llm_profile({"name": "Shared model", "model": "test-model", "api_key": "test-secret"})
    db.set_default_llm_profile(profile["id"])
    for uid in uids:
        db.access.assign("usr_admin", uid, "model", profile["id"])
    return profile["id"]


def test_existing_accounts_migrate_without_elevation_and_last_admin_is_protected(tmp_path):
    path = tmp_path / "legacy.db"
    conn = get_connection(path)
    conn.execute("CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,display_name TEXT,role TEXT DEFAULT 'admin',enabled INTEGER,created_at REAL,updated_at REAL)")
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO agents(id,name) VALUES('legacy_coord','Legacy coordinator')")
    conn.execute("INSERT INTO workplaces(id,name,kind,root_path) VALUES('legacy_folder','Legacy folder','local',?)", (str(tmp_path),))
    conn.execute("INSERT INTO sessions(id,coordinator_id,user_id,workplace_id) VALUES('legacy_chat','legacy_coord','old_admin','legacy_folder')")
    conn.executemany("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", [
        ("old_admin", "oldadmin", "unused", "Admin", "admin", 1, 1, 1),
        ("unknown", "oldunknown", "unused", "Unknown", "owner", 1, 2, 2),
    ])
    conn.commit()
    migrate(conn)
    migrate(conn)
    assert users.get_user(conn, "old_admin")["role"] == "admin"
    assert users.get_user(conn, "unknown")["role"] == "member"
    assert users.create_user(conn, {"username": "newmember", "password": "password123"})["role"] == "member"
    for update in ({"role": "member"}, {"enabled": False}):
        with pytest.raises(ValueError, match="last enabled Admin"):
            users.update_user(conn, "old_admin", update)
    with pytest.raises(ValueError, match="last enabled Admin"):
        users.delete_user(conn, "old_admin")
    with pytest.raises(ValueError):
        users.create_user(conn, {"username": "elevate", "password": "password123", "role": "owner"})
    conn.close()
    migrated = Store(path)
    assert migrated.access.can_use("old_admin", "unrestricted", "legacy_folder")
    assert migrated.get_session("legacy_chat")["execution_mode"] == "unrestricted"
    migrated.access.register_execution_stopper(lambda sid: None)
    migrated.access.revoke("old_admin", "old_admin", "unrestricted", "legacy_folder")
    migrated._conn.close()
    reopened = Store(path)
    assert not reopened.access.can_use("old_admin", "unrestricted", "legacy_folder")
    reopened._conn.close()


def test_owned_storage_sharing_activation_and_model_assignment(db):
    alice, bob = member(db, "alice"), member(db, "bob")
    model_for(db, alice)
    db.access.register_execution_stopper(lambda sid: None)
    sid = db.create_home_session(alice)["session_id"]
    bob_sid = db.create_home_session(bob)["session_id"]
    personal = db.get_session(sid)["workplace_id"]
    bob_personal = db.get_session(bob_sid)["workplace_id"]
    assert personal != bob_personal
    assert db.get_owned_session(sid, bob) is None
    assert personal not in {r["id"] for r in db.access.list_visible_workplaces(bob)}
    assert personal not in {r["id"] for r in db.access.list_visible_workplaces("usr_admin")}
    assert "root_path" not in db.access.list_visible_workplaces(alice)[0]
    project = db.access.create_project(bob, "Reference / name is not a path")
    db.access.share_project(bob, project["id"], alice, "read")
    initial = db.access.resolve_context(alice, sid)
    with pytest.raises(AccessDenied):
        db.access.authorize_resource(initial, project["id"])
    db.access.set_chat_access(alice, sid, personal, [project["id"]])
    context = db.access.resolve_context(alice, sid)
    assert db.access.authorize_resource(context, project["id"]).permission == "read"
    with pytest.raises(AccessDenied):
        db.access.authorize_resource(context, project["id"], write=True)
    with pytest.raises(AccessDenied):
        db.access.share_project(alice, project["id"], "usr_admin", "read_write")
    with pytest.raises(AccessDenied):
        db.access.set_chat_access(alice, sid, bob_personal)
    with pytest.raises(AccessDenied):
        db.access.require_session("usr_admin", sid)
    with pytest.raises(AccessUnavailable):
        db.access.resolve_context(bob, bob_sid)
    assert db.access.list_visible_models(bob) == []
    assert "test-secret" not in repr(db.access.list_visible_models(alice))


def test_unrestricted_requires_matching_grant_and_explicit_activation(db):
    uid = member(db, "memberone")
    model_for(db, uid)
    db.access.register_execution_stopper(lambda sid: None)
    sid = db.create_home_session(uid)["session_id"]
    active = db.get_session(sid)["workplace_id"]
    other = db.access.create_project(uid, "Other")["id"]
    with pytest.raises(AccessDenied):
        db.access.set_chat_access(uid, sid, active, execution_mode="unrestricted", unrestricted_acknowledged=True)
    with pytest.raises(AccessDenied):
        db.access.assign(uid, uid, "unrestricted", active)
    db.access.assign("usr_admin", uid, "unrestricted", other)
    with pytest.raises(AccessDenied):
        db.access.set_chat_access(uid, sid, active, execution_mode="unrestricted", unrestricted_acknowledged=True)
    db.access.assign("usr_admin", uid, "unrestricted", active)
    with pytest.raises(AccessDenied):
        db.access.set_chat_access(uid, sid, active, execution_mode="unrestricted")
    assert db.access.resolve_context(uid, sid).execution_mode == "restricted"
    db.access.set_chat_access(uid, sid, active, execution_mode="unrestricted", unrestricted_acknowledged=True)
    assert db.access.resolve_context(uid, sid).execution_mode == "unrestricted"
    db.access.revoke("usr_admin", uid, "unrestricted", active)
    with pytest.raises(AccessDenied):
        db.access.resolve_context(uid, sid)


def test_admin_defaults_to_host_without_grants_but_member_stays_restricted(db):
    uid = member(db, "defaultmember")
    model_for(db, uid)
    admin_sid = db.create_home_session("usr_admin")["session_id"]
    telegram_sid = db.get_or_create_session("main", "usr_admin", telegram_chat_id="42")
    for sid in (admin_sid, telegram_sid):
        ctx = db.access.resolve_context("usr_admin", sid)
        assert ctx.role == "admin" and ctx.execution_mode == "unrestricted"
        assert db.access.can_use("usr_admin", "unrestricted", ctx.destination_id)
    member_sid = db.create_home_session(uid)["session_id"]
    assert db.access.resolve_context(uid, member_sid).execution_mode == "restricted"
    assert not db.access.can_use(uid, "unrestricted", db.get_session(member_sid)["workplace_id"])
    assert not [g for g in db.access.list_grants("usr_admin", "usr_admin") if g["resource_type"] == "unrestricted"]


def test_admin_explicit_restricted_mode_and_revocation_are_not_overridden(db):
    db.access.register_execution_stopper(lambda sid: None)  # No work admitted.
    sid = db.create_home_session("usr_admin")["session_id"]
    active = db.get_session(sid)["workplace_id"]
    db.access.set_chat_access("usr_admin", sid, active, execution_mode="restricted")
    assert db.access.resolve_context("usr_admin", sid).execution_mode == "restricted"
    db.access.set_chat_access("usr_admin", sid, active, execution_mode="unrestricted")
    before = db.access.resolve_context("usr_admin", sid)
    stopped = []
    db.access.register_execution_stopper(stopped.append)
    db.access.revoke("usr_admin", "usr_admin", "unrestricted", active)
    assert sid in stopped
    with pytest.raises(AccessDenied):
        db.access.revalidate(before)
    new_sid = db.create_home_session("usr_admin")["session_id"]
    assert db.access.resolve_context("usr_admin", new_sid).execution_mode == "restricted"
    db.with_db(migrate)
    assert not db.access.can_use("usr_admin", "unrestricted", active)


def test_upgrade_restores_only_unconfigured_admin_personal_chats(db):
    db.access.register_execution_stopper(lambda sid: None)  # No work admitted.
    old = db.create_home_session("usr_admin")["session_id"]
    chosen = db.create_home_session("usr_admin")["session_id"]
    db.access.set_chat_access("usr_admin", chosen, db.get_session(chosen)["workplace_id"], execution_mode="restricted")
    # Persist the old release's automatic restricted default, not a user choice.
    def old_default(conn):
        conn.execute("UPDATE sessions SET execution_mode='restricted',access_generation=0 WHERE id=?", (old,))
        conn.commit()
    db.with_db(old_default)
    db.with_db(migrate)
    assert db.access.resolve_context("usr_admin", old).execution_mode == "unrestricted"
    assert db.get_session(old)["access_generation"] > 0
    assert db.access.resolve_context("usr_admin", chosen).execution_mode == "restricted"
    db.with_db(migrate)
    assert db.access.resolve_context("usr_admin", chosen).execution_mode == "restricted"


def test_revocation_stays_pending_without_backend_then_stops_real_managed_process(db):
    owner, recipient = member(db, "owner"), member(db, "recipient")
    model_for(db, recipient)
    project = db.access.create_project(owner, "Shared")
    db.access.share_project(owner, project["id"], recipient, "read_write")
    sid = db.create_swarm_session([], recipient, workplace_id=project["id"])
    context = db.access.resolve_context(recipient, sid)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with pytest.raises(AccessUnavailable, match="teardown backend"):
            db.access.revoke(owner, recipient, "workplace", project["id"])
        assert process.poll() is None  # No false report that execution was stopped.
        assert db.get_session(sid)["access_pending"]
        with pytest.raises(AccessUnavailable):
            db.access.revalidate(context)
        with pytest.raises(AccessDenied):
            db.access.workplace_permission(recipient, project["id"])

        def stop_session(session_id):
            assert session_id == sid
            process.terminate()
            process.wait(timeout=5)

        db.access.register_execution_stopper(stop_session)
        result = db.access.revoke(owner, recipient, "workplace", project["id"])
        assert result["state"] == "revoked"
        assert process.poll() is not None
        assert not db.get_session(sid)["access_pending"]
        with pytest.raises(AccessDenied):
            db.access.revalidate(context)
        assert any(row["action"] == "grant.revoke" for row in db.access.list_audit("usr_admin"))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_delegation_ceiling_current_grants_and_missing_context_fail_closed(db):
    uid = member(db, "delegateuser")
    mid = model_for(db, uid)
    db.access.register_execution_stopper(lambda sid: None)
    agent = db.create_agent({"name": "Specialist", "model_id": mid})
    unassigned = db.create_agent({"name": "Hidden specialist", "model_id": mid})
    db.access.assign("usr_admin", uid, "agent", agent["id"])
    sid = db.create_home_session(uid)["session_id"]
    parent = db.access.resolve_context(uid, sid)
    with pytest.raises(AccessDenied):
        db.access.require_tool(parent, "create_agent")
    with pytest.raises(AccessDenied):
        db.access.require_admin_action(parent)
    with pytest.raises(AccessDenied):
        db.access.resolve_context(uid, sid, unassigned["id"], parent=parent)
    # Deserializing async identity preserves the ceiling, never creates grants.
    ceiling = replace(parent, tool_ids=frozenset())
    restored = ExecutionContext.from_dict(ceiling.to_dict())
    delegated = db.access.resolve_context(uid, sid, agent["id"], parent=restored)
    assert delegated.tool_ids == frozenset()
    assert delegated.resources == parent.resources
    with pytest.raises(AccessDenied):
        db.access.require_tool(delegated, "bash")
    with pytest.raises(AccessDenied, match="identity"):
        current_execution()
    with execution_scope(parent):
        assert current_execution() == parent
    with pytest.raises(AccessDenied):
        current_execution()
    db.access.revoke("usr_admin", uid, "model", mid)
    with pytest.raises(AccessDenied):
        db.access.revalidate(parent)
    with pytest.raises(AccessDenied):
        db.access.resolve_context("web", sid)
    with pytest.raises(AccessDenied):
        db.access.resolve_context("tg_123", sid)


def test_account_downgrade_and_quota_barriers(db):
    uid = member(db, "secondadmin")
    db.update_user(uid, {"role": "admin"})
    sid = db.create_home_session(uid)["session_id"]
    with pytest.raises(AccessUnavailable):
        db.update_user(uid, {"role": "member"})
    assert db.get_session(sid)["access_pending"]
    with pytest.raises(AccessUnavailable):
        db.access.resolve_context(uid, sid)
    db.access.register_execution_stopper(lambda sid: None)
    db.update_user(uid, {"role": "member"})
    assert db.get_user(uid)["role"] == "member"
    with pytest.raises(AccessDenied):
        db.access.set_quota(uid, uid, {"cpu": 8})
    quota = db.access.set_quota("usr_admin", uid, {"cpu": 1, "memory_mb": 512, "max_concurrent_jobs": 1})
    assert quota == db.access.get_quota(uid)
    assert not quota.gpu_allowed
    with pytest.raises(ValueError):
        db.access.set_quota("usr_admin", uid, {"cpu": float("nan")})


def test_legacy_channel_execution_requires_explicit_current_admin_and_is_revocable(db):
    uid = member(db, "channeladmin")
    db.update_user(uid, {"role": "admin"})
    sid = db.get_or_create_session("main", "tg_123", telegram_chat_id="123")
    other = member(db, "channelmember")
    with pytest.raises(AccessDenied):
        db.access.resolve_context("tg_123", sid)
    with pytest.raises(AccessDenied):
        db.access.resolve_trusted_channel_context(other, sid)
    context = db.access.resolve_trusted_channel_context(uid, sid)
    assert context.trusted_channel
    assert context.user_id == uid
    assert context.session_owner_id == "tg_123"
    assert context.legacy_admin
    assert db.access.revalidate(context) == context
    # A non-login channel still runs under the explicit Admin principal; role
    # changes cannot forget its processes just because session owner is tg_*.
    with pytest.raises(AccessUnavailable):
        db.update_user(uid, {"role": "member"})
    assert db.get_session(sid)["access_pending"]
    stopped = []
    db.access.register_execution_stopper(lambda session_id: stopped.append(session_id))
    db.update_user(uid, {"role": "member"})
    assert sid in stopped
    with pytest.raises(AccessDenied):
        db.access.revalidate(context)


def test_registered_server_storage_is_not_a_restricted_member_mount(db):
    uid = member(db, "mountmember")
    model_for(db, uid)
    workplace = db.create_workplace({"name": "Server state", "kind": "local", "root_path": str(db._path.parent)})
    db.access.assign("usr_admin", uid, "workplace", workplace["id"], "read")
    sid = db.create_swarm_session([], uid, workplace_id=workplace["id"])
    with pytest.raises(AccessDenied, match="protected server storage"):
        db.access.resolve_context(uid, sid)
    project = db.access.create_project(uid, "Managed")
    with pytest.raises(ValueError, match="cannot be changed"):
        db.update_workplace(project["id"], {"root_path": str(db._path.parent)})
    remote = db.create_workplace({"name": "Remote", "kind": "tunnel"})
    assert remote["destination_id"] == remote["id"]
    db.access.assign("usr_admin", uid, "workplace", remote["id"], "read_write")
    remote_sid = db.create_swarm_session([], uid, workplace_id=remote["id"])
    with pytest.raises(AccessUnavailable, match="offline"):
        db.access.resolve_context(uid, remote_sid)


def test_personal_keys_and_schedule_identity_cannot_be_shared_or_overridden(db):
    alice, bob = member(db, "keyalice"), member(db, "keybob")
    model_for(db, alice)
    key = db.access.create_personal_api_key(bob)
    assert db.access.list_personal_api_keys(alice) == []
    with pytest.raises(AccessDenied):
        db.access.delete_personal_api_key(alice, key["id"])
    with pytest.raises(AccessDenied):
        db.access.update_personal_profile(alice, {"role": "admin"})
    sid = db.create_home_session(alice)["session_id"]
    context = db.access.resolve_context(alice, sid)
    schedule = db.access.create_schedule_for_context(context, {
        "name": "Owned schedule", "schedule": "every 1h", "owner_user_id": bob,
        "execution_context": {"user_id": bob},
    })
    assert schedule["owner_user_id"] == alice
    assert ExecutionContext.from_dict(schedule["execution_context"]) == context
    assert db.access.list_visible_schedules(bob) == []
    with pytest.raises(AccessDenied):
        db.access.require_schedule(bob, schedule["id"])
    db.update_schedule(schedule["id"], {"owner_user_id": bob, "execution_context": {"user_id": bob}})
    assert db.access.require_schedule(alice, schedule["id"])["execution_context"] == context.to_dict()


def test_cookie_and_api_key_roles_and_enabled_state_are_current(tmp_path):
    store.rebind(tmp_path / "http.db")
    uid = member(store, "httpmember")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-cookie-signing-only")

    @app.post("/login")
    def login(request: Request):
        user = store.authenticate("httpmember", "password123")
        request.session.update(auth=True, user_id=user["id"], role="admin")
        return {"ok": True}

    @app.get("/api/me", dependencies=[Depends(require_auth)])
    def me(request: Request):
        return authenticated_user(request)

    @app.get("/api/admin", dependencies=[Depends(require_admin)])
    def admin():
        return {"ok": True}

    client = TestClient(app)
    client.post("/login")
    assert client.get("/api/admin").status_code == 403  # Forged stale cookie role ignored.
    store.update_user(uid, {"role": "admin"})
    assert client.get("/api/admin").status_code == 200
    assert client.get("/api/admin", headers={"Authorization": "Bearer tomo_invalid"}).status_code == 401
    key = store.create_api_key(uid)["token"]
    key_client = TestClient(app)
    headers = {"Authorization": "Bearer " + key}
    assert key_client.get("/api/admin", headers=headers).status_code == 200
    store.update_user(uid, {"role": "member"})
    assert client.get("/api/admin").status_code == 403
    assert key_client.get("/api/admin", headers=headers).status_code == 403
    store.update_user(uid, {"enabled": False})
    assert client.get("/api/me").status_code == 401
    assert key_client.get("/api/me", headers=headers).status_code == 401
