"""Consolidated tests (merged from: test_agents.py, test_users.py, test_sessions_messages.py, test_schema.py).
- test_agents.py: Agent CRUD tests over the SQLite-backed store.
- test_users.py: Login accounts — passwords, bootstrap, CRUD guards, login POST.
- test_sessions_messages.py: Session + message history CRUD tests over the SQLite-backed store.
- test_schema.py: Schema migration tests for the foundation SQLite tables.
"""

from __future__ import annotations

import pytest
from app.services import store
from fastapi.testclient import TestClient
from app.core.deps import require_auth
from app.main import app
import time
import sqlite3
from app.models.schema import migrate


# --- from test_agents.py ---
def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "agents.db")


def test_list_agents_seeded(tmp_path) -> None:
    _rebind(tmp_path)
    agents = store.list_agents()
    assert {a["id"] for a in agents} == {"main", "ops", "coder", "research"}
    main = next(a for a in agents if a["id"] == "main")
    assert main["is_super"] is True
    assert main["busy"] is False


def test_get_agent_found_and_missing(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.get_agent("main")["name"] == "Tomo"
    assert store.get_agent("nope") is None


def test_create_agent(tmp_path) -> None:
    _rebind(tmp_path)
    agent = store.create_agent(
        {"id": "dev", "name": "Dev", "description": "d", "model_id": "gpt-4o-mini"}
    )
    assert agent["id"] == "dev"
    assert agent["enabled"] is True
    assert store.get_agent("dev")["name"] == "Dev"


def test_create_agent_duplicate_raises(tmp_path) -> None:
    _rebind(tmp_path)
    with pytest.raises(ValueError):
        store.create_agent({"id": "main", "name": "dup"})


def test_update_agent(tmp_path) -> None:
    _rebind(tmp_path)
    updated = store.update_agent("main", {"name": "Tomo2", "enabled": False})
    assert updated is not None
    assert updated["name"] == "Tomo2"
    assert updated["enabled"] is False
    assert store.update_agent("nope", {"name": "x"}) is None


def test_delete_agent(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.delete_agent("coder") is True
    assert store.get_agent("coder") is None
    assert store.delete_agent("coder") is False


def test_set_busy_is_session_scoped(tmp_path) -> None:
    _rebind(tmp_path)
    store.set_busy("ops", True, session_id="sess_a")
    # Global agent list / rail must not show busy from another session.
    by_id = {a["id"]: a for a in store.list_agents()}
    assert by_id["ops"]["busy"] is False
    assert store.is_agent_busy("ops", "sess_a") is True
    assert store.is_agent_busy("ops", "sess_b") is False
    store.set_busy("ops", False, session_id="sess_a")
    assert store.is_agent_busy("ops", "sess_a") is False


def test_busy_in_one_session_does_not_affect_another(tmp_path) -> None:
    _rebind(tmp_path)
    store.set_busy("main", True, session_id="sess_a")
    store.set_busy("main", True, session_id="sess_b")
    store.set_busy("main", False, session_id="sess_a")
    assert store.is_agent_busy("main", "sess_a") is False
    assert store.is_agent_busy("main", "sess_b") is True
    store.set_busy("main", False, session_id="sess_b")
    assert store.is_agent_busy("main", "sess_b") is False


# --- from test_users.py ---


def test_bootstrap_admin(tmp_path) -> None:
    store.rebind(tmp_path / "users-bootstrap.db")
    users = store.list_users()
    assert len(users) == 1
    assert users[0]["username"] == "admin"
    assert users[0]["enabled"] is True
    assert "password_hash" not in users[0]
    assert store.authenticate("admin", "tomo") is not None
    assert store.authenticate("admin", "nope") is None
    assert store.authenticate("ADMIN", "tomo") is not None  # case-insensitive


def test_create_and_auth_second_user(tmp_path) -> None:
    store.rebind(tmp_path / "users-create.db")
    u = store.create_user(
        {"username": "alice", "password": "password1", "display_name": "Alice"}
    )
    assert u["username"] == "alice"
    assert store.authenticate("alice", "password1")["id"] == u["id"]
    assert store.authenticate("alice", "wrong") is None


def test_cannot_delete_last_enabled(tmp_path) -> None:
    store.rebind(tmp_path / "users-last.db")
    admin = store.list_users()[0]
    try:
        store.delete_user(admin["id"])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "last enabled" in str(e).lower()


def test_cannot_disable_last_enabled(tmp_path) -> None:
    store.rebind(tmp_path / "users-disable.db")
    admin = store.list_users()[0]
    try:
        store.update_user(admin["id"], {"enabled": False})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "last enabled" in str(e).lower()


def test_delete_ok_when_another_enabled(tmp_path) -> None:
    store.rebind(tmp_path / "users-delete.db")
    store.create_user({"username": "bob", "password": "password1"})
    admin = store.get_user_by_username("admin")
    assert store.delete_user(admin["id"]) is True
    assert store.get_user_by_username("admin") is None


def _client(tmp_path) -> TestClient:
    store.rebind(tmp_path / "users-api.db")
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app)


def _cleanup() -> None:
    app.dependency_overrides.pop(require_auth, None)


def test_users_api_crud(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.get("/api/users")
        assert res.status_code == 200
        assert len(res.json()["users"]) == 1

        res = client.post(
            "/api/users",
            json={"username": "carol", "password": "password1", "display_name": "Carol"},
        )
        assert res.status_code == 200
        uid = res.json()["id"]
        assert "password_hash" not in res.json()

        res = client.put(
            f"/api/users/{uid}",
            json={"display_name": "Carol K", "password": "password2"},
        )
        assert res.status_code == 200
        assert res.json()["display_name"] == "Carol K"
        assert store.authenticate("carol", "password2") is not None

        res = client.delete(f"/api/users/{uid}")
        assert res.status_code == 200
        assert store.get_user(uid) is None
    finally:
        _cleanup()


def test_api_rejects_delete_last_enabled(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        admin = store.get_user_by_username("admin")
        res = client.delete(f"/api/users/{admin['id']}")
        assert res.status_code == 400
        assert "last enabled" in res.json()["detail"].lower()
    finally:
        _cleanup()


def test_login_page_does_not_hint_default_credentials(tmp_path) -> None:
    """Login UI must not advertise bootstrap username/password env names."""
    store.rebind(tmp_path / "users-login-page.db")
    client = TestClient(app)
    res = client.get("/login")
    assert res.status_code == 200
    body = res.text
    assert "TOMO_ADMIN_PASSWORD" not in body
    assert "placeholder=\"admin\"" not in body
    assert "placeholder='admin'" not in body


def test_openapi_docs_disabled(tmp_path) -> None:
    """Swagger/OpenAPI must not be public (create_app sets docs_url=None)."""
    store.rebind(tmp_path / "users-docs.db")
    client = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        res = client.get(path)
        assert res.status_code == 404, path


def test_login_post_success_and_fail(tmp_path) -> None:
    store.rebind(tmp_path / "users-login.db")
    # Real session middleware — no auth override.
    client = TestClient(app)
    res = client.post(
        "/login",
        data={"username": "admin", "password": "wrong", "next": "/"},
    )
    assert res.status_code == 401

    res = client.post(
        "/login",
        data={"username": "admin", "password": "tomo", "next": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/"

    res = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "tomo",
            "next": "https://evil.example/phish",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/"

    res = client.post(
        "/login",
        data={"username": "admin", "password": "tomo", "next": "/sessions"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/sessions"


def test_system_page_includes_accounts(tmp_path) -> None:
    client = _client(tmp_path)
    try:
        res = client.get("/system")
        assert res.status_code == 200
        assert b"Accounts" in res.content
        assert b"sec-users" in res.content
        assert b"admin" in res.content
    finally:
        _cleanup()


# --- from test_sessions_messages.py ---
def test_no_seeded_sessions(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.list_sessions() == []


def test_create_swarm_session_picks_super_coordinator(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main", "ops"], user_id="web")
    s = store.get_session(sid)
    assert s is not None
    # Live swarm roster includes all enabled seeded agents.
    assert s["agent_ids"][0] == "main"
    assert set(s["agent_ids"]) >= {"main", "ops", "coder", "research"}
    assert s["coordinator_id"] == "main"
    assert s["agent_id"] == "main"
    assert s["title"] == "New conversation"


def test_create_swarm_session_requires_valid_agent(tmp_path) -> None:
    _rebind(tmp_path)
    with pytest.raises(ValueError):
        store.create_swarm_session(["ghost"])


def test_get_session_missing(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.get_session("nope") is None


def test_append_and_list_history(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    store.append_session_history(sid, {"type": "user", "content": "hello", "ts": time.time()})
    store.append_session_history(
        sid, {"type": "final", "content": "hi there", "agent_id": "main", "ts": time.time()}
    )
    history = store.get_session_history(sid)
    assert [e["type"] for e in history] == ["user", "final"]
    assert history[0]["content"] == "hello"
    assert store.get_session(sid)["message_count"] == 2


def test_first_user_message_renames_session(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    store.append_session_history(
        sid, {"type": "user", "content": "Plan the Q3 launch carefully", "ts": time.time()}
    )
    assert store.get_session(sid)["title"] == "Plan the Q3 launch carefully"




def test_append_returns_resolved_title_once(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    first = store.append_session_history(
        sid, {"type": "user", "content": "Ship the darkroom fix tonight", "ts": time.time()}
    )
    assert first == "Ship the darkroom fix tonight"
    second = store.append_session_history(
        sid, {"type": "user", "content": "and also the docs", "ts": time.time()}
    )
    assert second is None
    assert store.get_session(sid)["title"] == "Ship the darkroom fix tonight"


def test_set_session_title(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    before = store.get_session(sid)["updated_at"]
    s = store.set_session_title(sid, "Q3 Launch Plan")
    assert s is not None
    assert s["title"] == "Q3 Launch Plan"
    assert store.get_session(sid)["title"] == "Q3 Launch Plan"
    assert store.get_session(sid)["updated_at"] >= before
    assert store.set_session_title("ses_missing", "Nope") is None


def test_clear_session_by_id(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    store.append_session_history(sid, {"type": "user", "content": "x"})
    store.clear_session_by_id(sid)
    assert store.get_session_history(sid) == []
    assert store.get_session(sid)["message_count"] == 0


def test_update_session_agents(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    s = store.update_session_agents(sid, ["main", "research"])
    assert s is not None
    assert set(s["agent_ids"]) >= {"main", "research"}
    assert s["coordinator_id"] == "main"
    with pytest.raises(ValueError):
        store.update_session_agents(sid, ["ghost"])


def test_update_session_agents_missing_returns_none(tmp_path) -> None:
    _rebind(tmp_path)
    assert store.update_session_agents("nope", ["main"]) is None


def test_get_or_create_session_idempotent(tmp_path) -> None:
    _rebind(tmp_path)
    sid1 = store.get_or_create_session("ops", "web")
    sid2 = store.get_or_create_session("ops", "web")
    assert sid1 == sid2
    s = store.get_session(sid1)
    assert s["agent_ids"] == ["ops"]
    assert s["coordinator_id"] == "ops"


def test_legacy_append_get_clear_history(tmp_path) -> None:
    _rebind(tmp_path)
    store.append_history("research", "web", {"type": "user", "content": "summarize", "ts": time.time()})
    store.append_history(
        "research", "web", {"type": "final", "content": "done", "agent_id": "research", "ts": time.time()}
    )
    history = store.get_history("research", "web")
    assert [e["type"] for e in history] == ["user", "final"]
    store.clear_session("research", "web")
    assert store.get_history("research", "web") == []


def test_delete_agent_removes_from_session_membership(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main", "ops"])
    store.delete_agent("ops")
    s = store.get_session(sid)
    assert s is not None
    assert s["agent_ids"] == ["main"]
    assert s["coordinator_id"] == "main"


def test_delete_agent_drops_solo_session(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["ops"])
    store.delete_agent("ops")
    assert store.get_session(sid) is None


def test_get_or_create_session_missing_agent_raises(tmp_path) -> None:
    """get_or_create_session must validate the agent (ValueError, not raw FK)."""
    _rebind(tmp_path)
    with pytest.raises(ValueError):
        store.get_or_create_session("ghost", "web")
    # No session was created for the missing agent.
    assert not any(s["coordinator_id"] == "ghost" for s in store.list_sessions())


def test_clear_session_noop_when_no_session(tmp_path) -> None:
    """clear_session(agent, user) must not invent an empty session (P3)."""
    _rebind(tmp_path)
    store.create_agent({"id": "custom", "name": "Custom"})
    before_ids = {s["id"] for s in store.list_sessions()}
    store.clear_session("custom", "web")  # no prior session -> no-op
    after_ids = {s["id"] for s in store.list_sessions()}
    assert before_ids == after_ids
    assert not any(s["coordinator_id"] == "custom" for s in store.list_sessions())


def test_clear_session_clears_existing_session(tmp_path) -> None:
    """When a session exists, clear_session clears messages but keeps the session."""
    _rebind(tmp_path)
    store.append_history("ops", "web", {"type": "user", "content": "hi", "ts": time.time()})
    store.append_history(
        "ops", "web", {"type": "final", "content": "hello", "agent_id": "ops", "ts": time.time()}
    )
    assert store.get_history("ops", "web") != []
    store.clear_session("ops", "web")
    assert store.get_history("ops", "web") == []
    # The session still exists (cleared, not deleted) — get_history reuses it.
    sessions = [s for s in store.list_sessions() if s["coordinator_id"] == "ops"]
    assert len(sessions) == 1
    assert sessions[0]["message_count"] == 0


def test_delete_session_removes_row(tmp_path) -> None:
    _rebind(tmp_path)
    sid = store.create_swarm_session(["main"])
    assert store.delete_session(sid) is True
    assert store.get_session(sid) is None
    assert store.delete_session(sid) is False


def test_prune_empty_draft_sessions_keeps_messaged_and_cleared(tmp_path) -> None:
    _rebind(tmp_path)
    draft = store.create_swarm_session(["main"])
    kept_open = store.create_swarm_session(["ops"])
    messaged = store.create_swarm_session(["research"])
    store.append_session_history(
        messaged, {"type": "user", "content": "hello there", "ts": time.time()}
    )
    # Cleared chat keeps custom title + message_count 0 — must not prune.
    cleared = store.create_swarm_session(["main"])
    store.append_session_history(
        cleared, {"type": "user", "content": "later cleared", "ts": time.time()}
    )
    store.clear_session_by_id(cleared)

    deleted = store.prune_empty_draft_sessions(keep_id=kept_open)
    assert draft in deleted
    assert kept_open not in deleted
    assert store.get_session(kept_open) is not None
    assert store.get_session(messaged) is not None
    assert store.get_session(cleared) is not None
    assert store.get_session(draft) is None


# --- from test_schema.py ---
EXPECTED_TABLES = {
    "agents",
    "sessions",
    "session_agents",
    "messages",
    "attachments",
    "settings",
    "agent_tools",
    "workplaces",
    "skills",
    "agent_skills",
    "schedules",
    "schedule_runs",
    "users",
    "llm_profiles",
    "api_keys",
    "usage_events",
    "mcp_servers",
    "mcp_items",
}


def test_migrate_creates_tables(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    migrate(conn)
    names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert EXPECTED_TABLES <= names


def test_migrate_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    migrate(conn)
    migrate(conn)  # second run must not error or duplicate tables
    names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert EXPECTED_TABLES <= names


def test_migrate_adds_reasoning_columns_to_legacy_tables(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE llm_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL DEFAULT '',
            api_key TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            coordinator_id TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'web',
            title TEXT NOT NULL DEFAULT '',
            message_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0
        );
        """
    )

    migrate(conn)

    profile_cols = {row[1] for row in conn.execute("PRAGMA table_info(llm_profiles)")}
    session_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert "reasoning_efforts_json" in profile_cols
    assert "reasoning_effort" in session_cols


def test_mcp_tables_have_runtime_columns(tmp_path):
    db = tmp_path / "mcp.db"
    conn = sqlite3.connect(db)
    migrate(conn)
    server_cols = {row[1] for row in conn.execute("PRAGMA table_info(mcp_servers)")}
    item_cols = {row[1] for row in conn.execute("PRAGMA table_info(mcp_items)")}
    assert {
        "id", "name", "transport", "command", "args_json", "url",
        "env_ciphertext", "headers_ciphertext", "enabled", "status",
        "status_message", "server_info_json", "capabilities_json",
        "last_connected_at", "last_discovered_at", "created_at", "updated_at",
        "supports_parallel_tool_calls",
    } <= server_cols
    assert {
        "id", "server_id", "kind", "runtime_id", "name", "title",
        "description", "uri", "mime_type", "schema_json", "metadata_json",
        "enabled", "created_at", "updated_at",
    } <= item_cols
    # Upgrade a pre-option database: saved servers survive, opt-in stays off.
    conn.execute("ALTER TABLE mcp_servers DROP COLUMN supports_parallel_tool_calls")
    conn.execute("INSERT INTO mcp_servers (id, name, transport, command) VALUES ('old', 'Old', 'stdio', 'echo')")
    migrate(conn)
    migrate(conn)
    assert conn.execute("SELECT command, supports_parallel_tool_calls FROM mcp_servers WHERE id='old'").fetchone() == ("echo", 0)


