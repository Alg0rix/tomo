"""Schema migration tests for the foundation SQLite tables."""

import sqlite3

from app.models.schema import migrate

EXPECTED_TABLES = {
    "agents",
    "sessions",
    "session_agents",
    "messages",
    "attachments",
    "settings",
    "agent_tools",
    "workplaces",
    "knowledge_entries",
    "skills",
    "agent_skills",
    "modules",
    "schedules",
    "schedule_runs",
    "users",
    "llm_profiles",
    "api_keys",
    "usage_events",
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
