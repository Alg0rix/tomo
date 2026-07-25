"""Schema migration tests for the foundation SQLite tables."""

import sqlite3

from app.models.schema import migrate

EXPECTED_TABLES = {"agents", "sessions", "session_agents", "messages", "settings"}


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
