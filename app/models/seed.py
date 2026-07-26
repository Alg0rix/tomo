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

from app.services.platform_data import seed_settings


def _now() -> float:
    return time.time()


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def _seed_agents(conn: sqlite3.Connection) -> None:
    base = _now()
    rows = [
        ("main", "Tomo", "Coordinator agent — routes work across the swarm and handles direct chat.", "gpt-4o-mini", 1, 1, 12, 3, 4, base - 86400 * 14),
        ("ops", "Ops", "Operations agent — deploys, runs shell tasks, watches workplaces.", "claude-3.5-sonnet", 1, 0, 8, 1, 6, base - 86400 * 9),
        ("research", "Research", "Research agent — fetches, summarizes, and stores artifacts.", "gpt-4o", 1, 0, 6, 1, 3, base - 86400 * 5),
        ("support", "Support", "Customer support agent — answers from the FAQ knowledge base.", "gpt-4o-mini", 0, 0, 5, 2, 2, base - 86400 * 2),
    ]
    conn.executemany(
        "INSERT INTO agents (id, name, description, model_id, enabled, is_super, "
        "tool_count, channel_count, skill_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
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
    rows = [(key, json.dumps(value)) for key, value in seed_settings().items()]
    conn.executemany("INSERT INTO settings (key, value_json) VALUES (?,?)", rows)


def seed_if_empty(conn: sqlite3.Connection) -> None:
    """Seed agents/sessions/settings only when the corresponding table is empty."""
    if _count(conn, "agents") == 0:
        _seed_agents(conn)
    if _count(conn, "sessions") == 0:
        _seed_sessions(conn)
    if _count(conn, "settings") == 0:
        _seed_settings(conn)
    conn.commit()
