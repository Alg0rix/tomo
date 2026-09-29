"""Durable, session-owned state for opted-in multi-agent runs."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def create_agent(
    conn: sqlite3.Connection, *, session_id: str, name: str, purpose: str,
    instructions: str, base_agent_id: str,
) -> dict[str, Any]:
    if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
        raise ValueError("Session not found")
    existing = conn.execute(
        "SELECT * FROM swarm_agents WHERE session_id=? AND name=?", (session_id, name)
    ).fetchone()
    if existing:
        return dict(existing)
    aid = _id("sag")
    conn.execute(
        "INSERT INTO swarm_agents (id,session_id,name,purpose,instructions,base_agent_id,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (aid, session_id, name, purpose, instructions, base_agent_id, time.time()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM swarm_agents WHERE id=?", (aid,)).fetchone())


def list_agents(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(
        "SELECT * FROM swarm_agents WHERE session_id=? ORDER BY created_at", (session_id,)
    )]


def update_agent_context(conn: sqlite3.Connection, agent_id: str, summary: str) -> None:
    conn.execute(
        "UPDATE swarm_agents SET context_summary=? WHERE id=?", (summary[-6000:], agent_id)
    )
    conn.commit()


def create_run(conn: sqlite3.Connection, session_id: str, request: str) -> str:
    if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
        raise ValueError("Session not found")
    rid = _id("srun")
    now = time.time()
    conn.execute(
        "INSERT INTO swarm_runs (id,session_id,request,created_at,updated_at) VALUES (?,?,?,?,?)",
        (rid, session_id, request, now, now),
    )
    conn.commit()
    return rid


def update_run(conn: sqlite3.Connection, run_id: str, status: str, result: str = "") -> None:
    conn.execute(
        "UPDATE swarm_runs SET status=?,result=?,updated_at=? WHERE id=?",
        (status, result, time.time(), run_id),
    )
    conn.commit()


def create_task(
    conn: sqlite3.Connection, *, run_id: str, agent_id: str, brief: str,
    depends_on: list[str], write_scope: list[str], tools: list[str],
) -> str:
    tid = _id("stask")
    now = time.time()
    conn.execute(
        "INSERT INTO swarm_tasks "
        "(id,run_id,agent_id,brief,depends_json,write_scope_json,tools_json,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (tid, run_id, agent_id, brief, json.dumps(depends_on), json.dumps(write_scope),
         json.dumps(tools), now, now),
    )
    conn.commit()
    return tid


def update_task(conn: sqlite3.Connection, task_id: str, status: str, result: str = "") -> None:
    conn.execute(
        "UPDATE swarm_tasks SET status=?,result=?,updated_at=? WHERE id=?",
        (status, result, time.time(), task_id),
    )
    conn.commit()


def append_event(
    conn: sqlite3.Connection, run_id: str, kind: str,
    payload: dict[str, Any], task_id: str = "",
) -> int:
    cur = conn.execute(
        "INSERT INTO swarm_events (run_id,task_id,kind,payload_json,created_at) VALUES (?,?,?,?,?)",
        (run_id, task_id, kind, json.dumps(payload, ensure_ascii=False), time.time()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_runs(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM swarm_runs WHERE session_id=? ORDER BY created_at DESC", (session_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM swarm_runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM swarm_tasks WHERE run_id=? ORDER BY created_at", (run_id,)
    ).fetchall()
    return [{**dict(row), "depends_on": json.loads(row["depends_json"]),
             "write_scope": json.loads(row["write_scope_json"]),
             "tools": json.loads(row["tools_json"])} for row in rows]


def list_events(conn: sqlite3.Connection, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM swarm_events WHERE run_id=? AND id>? ORDER BY id", (run_id, after_id)
    ).fetchall()
    return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]


def interrupt_inflight(conn: sqlite3.Connection) -> None:
    """A process restart must never replay an ambiguous external side effect."""
    now = time.time()
    conn.execute("UPDATE swarm_runs SET status='interrupted',updated_at=? WHERE status='running'", (now,))
    conn.execute("UPDATE swarm_tasks SET status='interrupted',updated_at=? WHERE status='running'", (now,))
    conn.commit()


def set_proposal(conn: sqlite3.Connection, session_id: str, request: str,
                 plan: dict[str, Any], summary: str) -> None:
    conn.execute(
        "INSERT INTO swarm_proposals (session_id,request,plan_json,summary,created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
        "request=excluded.request,plan_json=excluded.plan_json,summary=excluded.summary,"
        "created_at=excluded.created_at",
        (session_id, request, json.dumps(plan, ensure_ascii=False), summary, time.time()),
    )
    conn.commit()


def get_proposal(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM swarm_proposals WHERE session_id=?", (session_id,)
    ).fetchone()
    if not row:
        return None
    return {**dict(row), "plan": json.loads(row["plan_json"])}


def clear_proposal(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM swarm_proposals WHERE session_id=?", (session_id,))
    conn.commit()
