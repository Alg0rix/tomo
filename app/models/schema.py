"""Foundation SQLite schema: DDL and ``migrate()``.

Tables (per design spec §5 + Alpha Slice G):

* ``agents``         — swarm member definitions
* ``sessions``       — conversation sessions (coordinator + owner)
* ``session_agents`` — many-to-many session ↔ agent membership (ordered)
* ``messages``       — session history entries (ChatEntry replay format)
* ``settings``       — key/value platform settings (JSON-encoded values)
* ``agent_tools``    — per-agent tool enablement (Slice C; missing rows = all on)
* ``workplaces``     — local / SSH / tunnel execution contexts (Slice D)
* ``knowledge_entries`` — title/body/tags KB rows (Slice E; keyword recall)
* ``skills`` / ``agent_skills`` — skill catalog + per-agent links (Slice G)
* ``plugins``        — plugin metadata enable/disable (Slice G)
* ``schedules`` / ``schedule_runs`` — cron/interval jobs + run log (Slice G)

Booleans are stored as INTEGER (0/1); dict payloads (e.g. tool ``params``) are
JSON-encoded into ``params_json``. Foreign keys are enforced by
``app.models.db.get_connection`` (``PRAGMA foreign_keys = ON``).
"""

from __future__ import annotations

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    model_id      TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1,
    is_super      INTEGER NOT NULL DEFAULT 0,
    tool_count    INTEGER NOT NULL DEFAULT 0,
    channel_count INTEGER NOT NULL DEFAULT 0,
    skill_count   INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_profiles (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    base_url   TEXT NOT NULL DEFAULT '',
    api_key    TEXT NOT NULL DEFAULT '',
    model      TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    coordinator_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    user_id        TEXT NOT NULL DEFAULT 'web',
    title          TEXT NOT NULL DEFAULT '',
    message_count  INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_agents (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    agent_id   TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, agent_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    type        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    agent_id    TEXT,
    function    TEXT,
    params_json TEXT,
    error       INTEGER NOT NULL DEFAULT 0,
    ts          REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tools (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool_id  TEXT NOT NULL,
    enabled  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (agent_id, tool_id)
);

CREATE TABLE IF NOT EXISTS workplaces (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'offline',
    host         TEXT NOT NULL DEFAULT '',
    root_path    TEXT NOT NULL DEFAULT '',
    ssh_host     TEXT NOT NULL DEFAULT '',
    ssh_port     INTEGER NOT NULL DEFAULT 22,
    ssh_user     TEXT NOT NULL DEFAULT '',
    ssh_password TEXT NOT NULL DEFAULT '',
    ssh_key      TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    tags_json  TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS skills (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '1.0',
    enabled     INTEGER NOT NULL DEFAULT 1,
    tool_count  INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agent_skills (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, skill_id)
);

CREATE TABLE IF NOT EXISTS plugins (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL DEFAULT '1.0',
    enabled     INTEGER NOT NULL DEFAULT 1,
    has_ui      INTEGER NOT NULL DEFAULT 0,
    ui_path     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schedules (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    agent_id          TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    cron              TEXT NOT NULL DEFAULT '',
    interval_seconds  INTEGER NOT NULL DEFAULT 0,
    message           TEXT NOT NULL DEFAULT '',
    enabled           INTEGER NOT NULL DEFAULT 1,
    last_run          REAL,
    next_run          REAL,
    created_at        REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id           TEXT PRIMARY KEY,
    schedule_id  TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    session_id   TEXT,
    status       TEXT NOT NULL DEFAULT 'ok',
    error        TEXT NOT NULL DEFAULT '',
    started_at   REAL NOT NULL DEFAULT 0,
    finished_at  REAL
);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Create foundation tables if missing, then commit.

    Idempotent: safe to call on an already-migrated database. ``CREATE TABLE
    IF NOT EXISTS`` will not alter an existing ``agents`` table, so a pre-Slice-A
    database gets the ``role`` / ``workplace_id`` columns via explicit
    ``ALTER TABLE``.
    """
    conn.executescript(_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agents)")}
    if "role" not in cols:
        conn.execute("ALTER TABLE agents ADD COLUMN role TEXT NOT NULL DEFAULT ''")
    if "workplace_id" not in cols:
        conn.execute(
            "ALTER TABLE agents ADD COLUMN workplace_id TEXT NOT NULL DEFAULT ''"
        )
    conn.commit()
