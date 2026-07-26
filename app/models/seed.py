"""Seed demo agents, sessions, and settings into an empty foundation DB.

Called by the store facade on init via :func:`seed_if_empty`. Seeding is
idempotent: each section only runs when its table is empty, so an already
populated DB is left untouched. There is no migrate-from-JSON path — an empty
DB is seeded fresh.
"""

from __future__ import annotations

import json
import sqlite3
import time

# Demo sessions reference these agent ids as coordinators/members. Sessions are
# only seeded when all of them already exist, so an empty ``sessions`` table
# never FK-fails against a non-demo (custom-only) ``agents`` table.
_REQUIRED_SESSION_AGENTS = ("main", "ops", "research")


def _now() -> float:
    return time.time()


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def _has_agent(conn: sqlite3.Connection, agent_id: str) -> bool:
    return conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone() is not None


def _seed_agents(conn: sqlite3.Connection) -> None:
    base = _now()
    # model_id is a profile id (empty = use default); roles are free-text labels.
    rows = [
        ("main", "Tomo", "Coordinator agent — routes swarm chat and hands off via delegate / @mention.", "", "coordinator", 1, 1, 12, 3, 4, base - 86400 * 14),
        ("ops", "Ops", "Operations agent — deploys, runs shell tasks, watches workplaces.", "", "ops", 1, 0, 8, 1, 6, base - 86400 * 9),
        ("research", "Research", "Research agent — fetches, summarizes, and stores artifacts.", "", "research", 1, 0, 6, 1, 3, base - 86400 * 5),
        ("support", "Support", "Customer support agent — answers from the FAQ knowledge base.", "", "support", 0, 0, 5, 2, 2, base - 86400 * 2),
    ]
    conn.executemany(
        "INSERT INTO agents (id, name, description, model_id, role, enabled, is_super, "
        "tool_count, channel_count, skill_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def _seed_sessions(conn: sqlite3.Connection) -> None:
    base = _now()
    sessions = [
        ("ses_001", "main", "web", "Onboarding Q3 vendors", 8, base - 7200, base - 3600, ["main", "ops", "research"]),
        ("ses_002", "ops", "web", "Deploy staging cluster", 23, base - 9000, base - 7200, ["ops"]),
        ("ses_003", "research", "web", "Summarize competitor pricing", 11, base - 22000, base - 18000, ["research"]),
    ]
    for sid, coord, uid, title, count, created, updated, agent_ids in sessions:
        conn.execute(
            "INSERT INTO sessions (id, coordinator_id, user_id, title, message_count, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (sid, coord, uid, title, count, created, updated),
        )
        conn.executemany(
            "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
            [(sid, aid, pos) for pos, aid in enumerate(agent_ids)],
        )


def _seed_settings(conn: sqlite3.Connection) -> None:
    # Lazy import: ``seed`` is imported during ``Store`` init, and a module-level
    # import of ``app.services.platform_data`` would pull the ``app.services``
    # package ``__init__`` (which itself imports the store). Importing here keeps
    # ``app.models.seed`` free of that circular dependency at load time.
    from app.services.platform_data import seed_settings

    rows = [(key, json.dumps(value)) for key, value in seed_settings().items()]
    conn.executemany("INSERT INTO settings (key, value_json) VALUES (?,?)", rows)


def seed_if_empty(conn: sqlite3.Connection) -> None:
    """Seed agents/sessions/settings only when the corresponding table is empty.

    Demo sessions are only seeded when every required coordinator/member agent
    (``main``, ``ops``, ``research``) already exists — otherwise the session
    inserts would FK-fail (e.g. a DB reopened with only custom agents). A failed
    seed is rolled back so the connection/transaction is never left dirty.
    """
    try:
        if _count(conn, "agents") == 0:
            _seed_agents(conn)
        if _count(conn, "sessions") == 0:
            if all(_has_agent(conn, aid) for aid in _REQUIRED_SESSION_AGENTS):
                _seed_sessions(conn)
        if _count(conn, "settings") == 0:
            _seed_settings(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
