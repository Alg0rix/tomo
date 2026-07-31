"""Regression tests for ``seed_if_empty`` FK safety and idempotency.

Covers the P1 bug: when ``agents`` is non-empty but lacks the demo coordinator/
member ids (``main``/``ops``/``research``), seeding must not FK-fail and crash
``Store._open`` / ``rebind``. Sessions are never seeded.
"""

from __future__ import annotations

import sqlite3

from app.models.db import get_connection
from app.models.schema import migrate
from app.models.seed import seed_if_empty
from app.services import store


def _fresh_conn(tmp_path) -> sqlite3.Connection:
    conn = get_connection(tmp_path / "seed.db")
    migrate(conn)
    return conn


def test_seed_if_empty_populates_demo_data(tmp_path) -> None:
    conn = _fresh_conn(tmp_path)
    seed_if_empty(conn)
    agent_ids = {r["id"] for r in conn.execute("SELECT id FROM agents")}
    assert {"main", "ops", "coder", "research"} <= agent_ids
    assert conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM settings").fetchone()["c"] > 0
    kb_ids = {r["id"] for r in conn.execute("SELECT id FROM knowledge_entries")}
    assert "kb_vendor_deadline" in kb_ids
    conn.close()


def test_seed_if_empty_idempotent(tmp_path) -> None:
    conn = _fresh_conn(tmp_path)
    seed_if_empty(conn)
    n_agents = conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"]
    n_sessions = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
    n_settings = conn.execute("SELECT COUNT(*) AS c FROM settings").fetchone()["c"]
    n_kb = conn.execute("SELECT COUNT(*) AS c FROM knowledge_entries").fetchone()["c"]
    seed_if_empty(conn)  # all tables non-empty -> no-op
    assert conn.execute("SELECT COUNT(*) AS c FROM agents").fetchone()["c"] == n_agents
    assert conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"] == n_sessions
    assert conn.execute("SELECT COUNT(*) AS c FROM settings").fetchone()["c"] == n_settings
    assert conn.execute("SELECT COUNT(*) AS c FROM knowledge_entries").fetchone()["c"] == n_kb
    conn.close()


def test_seed_if_empty_skips_demo_sessions_when_agents_missing(tmp_path) -> None:
    """Custom-only agents must not FK-crash on seed (P1). Sessions stay empty."""
    conn = _fresh_conn(tmp_path)
    # agents non-empty but demo ids absent; sessions table empty.
    conn.execute(
        "INSERT INTO agents (id, name, description, model_id, enabled, is_super, "
        "tool_count, channel_count, skill_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("custom", "Custom", "", "", 1, 0, 0, 0, 0, 0),
    )
    conn.commit()

    seed_if_empty(conn)  # must not raise FOREIGN KEY IntegrityError

    assert conn.execute("SELECT id FROM sessions").fetchall() == []
    # custom agent untouched; settings still seeded from the empty settings table.
    assert conn.execute("SELECT 1 FROM agents WHERE id='custom'").fetchone() is not None
    assert conn.execute("SELECT COUNT(*) AS c FROM settings").fetchone()["c"] > 0
    conn.close()


def test_seed_if_empty_never_seeds_sessions(tmp_path) -> None:
    """Even when required demo agents exist, sessions are not seeded."""
    conn = _fresh_conn(tmp_path)
    base = 0
    for aid in ("main", "ops", "coder", "research"):
        conn.execute(
            "INSERT INTO agents (id, name, description, model_id, enabled, is_super, "
            "tool_count, channel_count, skill_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, aid, "", "", 1, 0, 0, 0, 0, base),
        )
    conn.commit()
    seed_if_empty(conn)
    assert conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"] == 0
    conn.close()


def test_rebind_with_custom_only_agents_no_crash(tmp_path) -> None:
    """End-to-end P1 repro: delete demo agents, add a custom one, rebind same DB."""
    db = tmp_path / "rebind.db"
    store.rebind(db)
    for aid in ("main", "ops", "coder", "research"):
        store.delete_agent(aid)
    store.create_agent({"id": "custom", "name": "Custom"})

    # Rebind the same DB file -> seed_if_empty must not FK-fail.
    store.rebind(db)

    assert store.get_agent("custom") is not None
    assert store.list_sessions() == []
    assert store.get_settings()["setup_complete"] is True
