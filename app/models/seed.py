"""Seed demo agents, settings, and platform entities into an empty DB.

Called by the store facade on init via :func:`seed_if_empty`. Seeding is
idempotent: each section only runs when its table is empty, so an already
populated DB is left untouched. There is no migrate-from-JSON path — an empty
DB is seeded fresh.

Sessions are not seeded: the sidebar starts empty and chats are created on
first message.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from app.models.mixins.schedules import interval_from_cron

# Default swarm: coordinator + Ops + Coder + Research (no Support).
_SEED_AGENT_IDS = ("main", "ops", "coder", "research")

# Explicit tool enablement for specialists. Missing agent → no rows → all tools on.
_AGENT_TOOLS: dict[str, frozenset[str]] = {
    "ops": frozenset(
        {
            "bash",
            "process",
            "list_dir",
            "list_workplaces",
            "read_file",
            "write_file",
            "search_files",
            "todo",
            "clarify",
            "recall",
            "remember",
            "agent_state",
            "save_artifact",
            "list_skills",
            "use_skill",
            "manage_skill",
            "portal",
            "delegate",
        }
    ),
    "coder": frozenset(
        {
            "read_file",
            "write_file",
            "str_replace",
            "patch",
            "list_dir",
            "list_workplaces",
            "search_files",
            "delete_file",
            "bash",
            "runpy",
            "todo",
            "session_search",
            "clarify",
            "recall",
            "remember",
            "agent_state",
            "save_artifact",
            "list_skills",
            "use_skill",
            "manage_skill",
            "portal",
            "delegate",
        }
    ),
    "research": frozenset(
        {
            "web_search",
            "web_fetch",
            "recall",
            "remember",
            "agent_state",
            "save_artifact",
            "todo",
            "clarify",
            "session_search",
            "list_workplaces",
            "read_file",
            "write_file",
            "list_skills",
            "use_skill",
            "manage_skill",
            "delegate",
        }
    ),
}


def _now() -> float:
    return time.time()


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def _has_agent(conn: sqlite3.Connection, agent_id: str) -> bool:
    return conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone() is not None


def _known_tool_ids() -> list[str]:
    tools_dir = Path(__file__).resolve().parents[1] / "tools"
    return sorted(p.stem for p in tools_dir.glob("*.json"))


def _seed_agent_homes() -> None:
    """Copy shipped SYSTEM.md into ``$TOMO_HOME/agents/<id>/`` when missing."""
    try:
        from app.core import config, home
    except Exception:
        return
    defaults = config.REPO_ROOT / "defaults" / "agents"
    for aid in ("ops", "coder", "research"):
        src = defaults / aid / "SYSTEM.md"
        if not src.is_file():
            continue
        try:
            adir = home.agent_dir(aid)
            adir.mkdir(parents=True, exist_ok=True)
            home.agent_knowledge_dir(aid).mkdir(parents=True, exist_ok=True)
            home.agent_work_dir(aid).mkdir(parents=True, exist_ok=True)
            dest = home.agent_system_path(aid)
            if not dest.exists():
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass


def _seed_agent_tools(conn: sqlite3.Connection) -> None:
    """Persist specialist tool allow-lists; coordinator keeps default (all on)."""
    known = _known_tool_ids()
    if not known:
        return
    for agent_id, enabled in _AGENT_TOOLS.items():
        if not _has_agent(conn, agent_id):
            continue
        conn.execute("DELETE FROM agent_tools WHERE agent_id=?", (agent_id,))
        on_count = 0
        for tool_id in known:
            is_on = tool_id in enabled
            conn.execute(
                "INSERT INTO agent_tools (agent_id, tool_id, enabled) VALUES (?,?,?)",
                (agent_id, tool_id, 1 if is_on else 0),
            )
            if is_on:
                on_count += 1
        conn.execute(
            "UPDATE agents SET tool_count=? WHERE id=?",
            (on_count, agent_id),
        )


def _seed_agents(conn: sqlite3.Connection) -> None:
    base = _now()
    # model_id is a profile id (empty = use default); roles are free-text labels.
    # tool_count placeholders are overwritten by ``_seed_agent_tools``.
    rows = [
        (
            "main",
            "Tomo",
            "Swarm coordinator — local work, routing, and delegate / @mention handoffs.",
            "",
            "coordinator",
            1,
            1,
            0,
            0,
            0,
            base - 86400 * 14,
        ),
        (
            "ops",
            "Ops",
            "Operations — shell, processes, and tunnel/SSH workplaces; verify with commands.",
            "",
            "ops",
            1,
            0,
            0,
            0,
            0,
            base - 86400 * 9,
        ),
        (
            "coder",
            "Coder",
            "Software — explore, edit, and verify code with small diffs and tests.",
            "",
            "coder",
            1,
            0,
            0,
            0,
            0,
            base - 86400 * 5,
        ),
        (
            "research",
            "Research",
            "Research — web search/fetch, synthesize with sources, remember durable facts.",
            "",
            "research",
            1,
            0,
            0,
            0,
            0,
            base - 86400 * 2,
        ),
    ]
    conn.executemany(
        "INSERT INTO agents (id, name, description, model_id, role, enabled, is_super, "
        "tool_count, channel_count, skill_count, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    # Ops can reach every tunnel connector by default (not 1 agent = 1 workplace).
    try:
        conn.execute(
            "UPDATE agents SET workplace_scope='all_tunnels' WHERE id='ops'"
        )
    except sqlite3.OperationalError:
        pass
    _seed_agent_tools(conn)
    _seed_agent_homes()


def _seed_settings(conn: sqlite3.Connection) -> None:
    # Lazy import: ``seed`` is imported during ``Store`` init, and a module-level
    # import of ``app.services.platform_data`` would pull the ``app.services``
    # package ``__init__`` (which itself imports the store). Importing here keeps
    # ``app.models.seed`` free of that circular dependency at load time.
    from app.services.platform_data import seed_settings

    rows = [(key, json.dumps(value)) for key, value in seed_settings().items()]
    conn.executemany("INSERT INTO settings (key, value_json) VALUES (?,?)", rows)


def _seed_knowledge_entries(conn: sqlite3.Connection) -> None:
    """Seed a small FAQ-style KB for recall demos (Slice E)."""
    base = _now()
    rows = [
        (
            "kb_vendor_deadline",
            "Q3 vendor onboarding deadline",
            "The Q3 vendor onboarding deadline is October 15, 2026. All vendor "
            "packets must be submitted to Ops by that date.",
            json.dumps(["vendors", "onboarding", "deadline", "q3"]),
            base - 86400 * 3,
            base - 86400 * 3,
        ),
        (
            "kb_support_hours",
            "On-call business hours",
            "Human on-call coverage is Monday–Friday, 09:00–18:00 local time. "
            "Urgent production incidents can page Ops outside those hours.",
            json.dumps(["oncall", "hours", "ops"]),
            base - 86400 * 2,
            base - 86400 * 2,
        ),
        (
            "kb_staging_cluster",
            "Staging cluster hostname",
            "The staging Kubernetes cluster hostname is staging.tomo.internal. "
            "Deployments require the Ops agent workplace.",
            json.dumps(["staging", "ops", "cluster"]),
            base - 86400,
            base - 86400,
        ),
    ]
    conn.executemany(
        "INSERT INTO knowledge_entries (id, title, body, tags_json, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    try:
        from app.runtime.memory.fts import rebuild_knowledge_fts

        rebuild_knowledge_fts(conn)
    except Exception:
        pass


def _seed_skills(conn: sqlite3.Connection) -> None:
    """Discover filesystem skills into the catalog (no fake placeholder rows)."""
    from app.extensions.skills import sync_skills_to_db

    sync_skills_to_db(conn)
    for agent_id in _SEED_AGENT_IDS:
        if not _has_agent(conn, agent_id):
            continue
        conn.execute(
            "UPDATE agents SET skill_count=? WHERE id=?",
            (0, agent_id),
        )


def _seed_plugins(conn: sqlite3.Connection) -> None:
    from app.services.platform_data import seed_plugins

    base = _now()
    for i, p in enumerate(seed_plugins()):
        conn.execute(
            "INSERT INTO plugins (id, name, description, version, enabled, has_ui, "
            "ui_path, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                p["id"],
                p["name"],
                p["description"],
                p.get("version", "1.0"),
                1 if p.get("enabled", True) else 0,
                1 if p.get("has_ui") else 0,
                p.get("ui_path") or "",
                base - 86400 * (3 - i),
            ),
        )


def _seed_schedules(conn: sqlite3.Connection) -> None:
    """Seed demo schedules only when referenced agents exist."""
    from app.services.platform_data import seed_schedules

    base = _now()
    for s in seed_schedules():
        agent_id = s.get("agent_id") or ""
        if not _has_agent(conn, agent_id):
            continue
        cron = s.get("cron") or ""
        interval = interval_from_cron(cron)
        enabled = 1 if s.get("enabled", True) else 0
        next_run = s.get("next_run") if enabled else None
        conn.execute(
            "INSERT INTO schedules (id, name, agent_id, cron, interval_seconds, message, "
            "enabled, last_run, next_run, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                s["id"],
                s["name"],
                agent_id,
                cron,
                interval,
                f"[schedule] {s['name']}",
                enabled,
                s.get("last_run"),
                next_run,
                base - 3600,
            ),
        )


def seed_if_empty(conn: sqlite3.Connection) -> None:
    """Seed core + platform tables only when the corresponding table is empty.

    Demo schedules are only seeded when their agent ids already exist —
    otherwise inserts would FK-fail (e.g. a DB reopened with only custom agents).
    Sessions are never seeded. A failed seed is rolled back so the
    connection/transaction is never left dirty.
    """
    try:
        if _count(conn, "agents") == 0:
            _seed_agents(conn)
        if _count(conn, "settings") == 0:
            _seed_settings(conn)
        if _count(conn, "knowledge_entries") == 0:
            _seed_knowledge_entries(conn)
        if _count(conn, "skills") == 0:
            _seed_skills(conn)
        if _count(conn, "plugins") == 0:
            _seed_plugins(conn)
        if _count(conn, "schedules") == 0:
            _seed_schedules(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
