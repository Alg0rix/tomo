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
* ``modules``        — optional module catalog enable/disable
* ``usage_events``   — Token Monitor turn/token ledger
* ``schedules`` / ``schedule_runs`` — cron/interval jobs + run log (Slice G)
* ``users``           — login accounts (username + scrypt password hash)
* ``api_keys``        — per-account Bearer tokens for ``/api/*`` access

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
    workplace_id   TEXT NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS attachments (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    filename      TEXT NOT NULL DEFAULT '',
    original_name TEXT NOT NULL DEFAULT '',
    mime_type     TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    file_path     TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL DEFAULT 0
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
    id                     TEXT PRIMARY KEY,
    name                   TEXT NOT NULL,
    kind                   TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'offline',
    host                   TEXT NOT NULL DEFAULT '',
    root_path              TEXT NOT NULL DEFAULT '',
    ssh_host               TEXT NOT NULL DEFAULT '',
    ssh_port               INTEGER NOT NULL DEFAULT 22,
    ssh_user               TEXT NOT NULL DEFAULT '',
    ssh_password           TEXT NOT NULL DEFAULT '',
    ssh_key                TEXT NOT NULL DEFAULT '',
    pairing_code           TEXT NOT NULL DEFAULT '',
    pairing_expires_at     REAL NOT NULL DEFAULT 0,
    connector_token        TEXT NOT NULL DEFAULT '',
    connector_last_seen_at REAL NOT NULL DEFAULT 0,
    connector_version      TEXT NOT NULL DEFAULT '',
    connector_hostname     TEXT NOT NULL DEFAULT '',
    connector_platform     TEXT NOT NULL DEFAULT '',
    connector_remote_ip    TEXT NOT NULL DEFAULT '',
    created_at             REAL NOT NULL DEFAULT 0,
    updated_at             REAL NOT NULL DEFAULT 0
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
    created_at  REAL NOT NULL DEFAULT 0,
    path        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_skills (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, skill_id)
);

CREATE TABLE IF NOT EXISTS modules (
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
    created_at        REAL NOT NULL DEFAULT 0,
    schedule_kind     TEXT NOT NULL DEFAULT 'interval',
    schedule_display  TEXT NOT NULL DEFAULT '',
    schedule_expr     TEXT NOT NULL DEFAULT '',
    state             TEXT NOT NULL DEFAULT 'scheduled',
    pause_reason      TEXT NOT NULL DEFAULT '',
    repeat_times      INTEGER,
    run_count         INTEGER NOT NULL DEFAULT 0,
    claim_until       REAL
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

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'admin',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    REAL NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL DEFAULT '',
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    created_at   REAL NOT NULL DEFAULT 0,
    last_used_at REAL
);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    scope      TEXT NOT NULL,
    ref_id     TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    dims       INTEGER NOT NULL DEFAULT 0,
    vector_json TEXT NOT NULL DEFAULT '[]',
    text_hash  TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, ref_id)
);

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    summary    TEXT NOT NULL DEFAULT '',
    message_count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS usage_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT NOT NULL,
    agent_id           TEXT NOT NULL DEFAULT '',
    created_at         REAL NOT NULL DEFAULT 0,
    turns              INTEGER NOT NULL DEFAULT 1,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    message_preview    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_usage_events_created
    ON usage_events(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_agent
    ON usage_events(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_session
    ON usage_events(session_id, created_at);

CREATE TABLE IF NOT EXISTS artifacts (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    path       TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'file',
    session_id TEXT NOT NULL DEFAULT '',
    agent_id   TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    meta_json  TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, key)
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
    if "workplace_scope" not in cols:
        conn.execute(
            "ALTER TABLE agents ADD COLUMN workplace_scope TEXT NOT NULL DEFAULT 'single'"
        )
    if "workplace_ids_json" not in cols:
        conn.execute(
            "ALTER TABLE agents ADD COLUMN workplace_ids_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "artifacts_enabled" not in cols:
        conn.execute(
            "ALTER TABLE agents ADD COLUMN artifacts_enabled INTEGER NOT NULL DEFAULT 1"
        )
    sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "workplace_id" not in sess_cols:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN workplace_id TEXT NOT NULL DEFAULT ''"
        )
    # Connector tunnel columns (idempotent ALTER for pre-connector DBs).
    wp_cols = {r[1] for r in conn.execute("PRAGMA table_info(workplaces)")}
    _wp_alters = {
        "pairing_code": "ALTER TABLE workplaces ADD COLUMN pairing_code TEXT NOT NULL DEFAULT ''",
        "pairing_expires_at": "ALTER TABLE workplaces ADD COLUMN pairing_expires_at REAL NOT NULL DEFAULT 0",
        "connector_token": "ALTER TABLE workplaces ADD COLUMN connector_token TEXT NOT NULL DEFAULT ''",
        "connector_last_seen_at": "ALTER TABLE workplaces ADD COLUMN connector_last_seen_at REAL NOT NULL DEFAULT 0",
        "connector_version": "ALTER TABLE workplaces ADD COLUMN connector_version TEXT NOT NULL DEFAULT ''",
        "connector_hostname": "ALTER TABLE workplaces ADD COLUMN connector_hostname TEXT NOT NULL DEFAULT ''",
        "connector_platform": "ALTER TABLE workplaces ADD COLUMN connector_platform TEXT NOT NULL DEFAULT ''",
        "connector_remote_ip": "ALTER TABLE workplaces ADD COLUMN connector_remote_ip TEXT NOT NULL DEFAULT ''",
    }
    for col, ddl in _wp_alters.items():
        if col not in wp_cols:
            conn.execute(ddl)
    skill_cols = {r[1] for r in conn.execute("PRAGMA table_info(skills)")}
    if "path" not in skill_cols:
        conn.execute("ALTER TABLE skills ADD COLUMN path TEXT NOT NULL DEFAULT ''")
    if "source" not in skill_cols:
        conn.execute("ALTER TABLE skills ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    if "use_count" not in skill_cols:
        conn.execute(
            "ALTER TABLE skills ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0"
        )
    if "last_used_at" not in skill_cols:
        conn.execute(
            "ALTER TABLE skills ADD COLUMN last_used_at REAL NOT NULL DEFAULT 0"
        )

    # plugins → modules rename (Alpha catalog rename).
    table_names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "plugins" in table_names and "modules" not in table_names:
        conn.execute("ALTER TABLE plugins RENAME TO modules")
    # Connector is a first-class feature, not a catalog module.
    if "modules" in {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        conn.execute("DELETE FROM modules WHERE id='connector'")

    # FTS5 indexes for lexical retrieval (built into SQLite).
    fts_names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' OR type='virtual'"
        )
    }
    if "knowledge_fts" not in fts_names:
        conn.execute(
            "CREATE VIRTUAL TABLE knowledge_fts USING fts5("
            "id UNINDEXED, title, body, tags, tokenize='porter')"
        )
    if "messages_fts" not in fts_names:
        conn.execute(
            "CREATE VIRTUAL TABLE messages_fts USING fts5("
            "msg_id UNINDEXED, session_id UNINDEXED, type UNINDEXED, "
            "content, tokenize='porter')"
        )

    # Schedule harness columns (idempotent ALTER).
    sch_cols = {r[1] for r in conn.execute("PRAGMA table_info(schedules)")}
    _sch_alters = {
        "schedule_kind": "ALTER TABLE schedules ADD COLUMN schedule_kind TEXT NOT NULL DEFAULT 'interval'",
        "schedule_display": "ALTER TABLE schedules ADD COLUMN schedule_display TEXT NOT NULL DEFAULT ''",
        "schedule_expr": "ALTER TABLE schedules ADD COLUMN schedule_expr TEXT NOT NULL DEFAULT ''",
        "state": "ALTER TABLE schedules ADD COLUMN state TEXT NOT NULL DEFAULT 'scheduled'",
        "pause_reason": "ALTER TABLE schedules ADD COLUMN pause_reason TEXT NOT NULL DEFAULT ''",
        "repeat_times": "ALTER TABLE schedules ADD COLUMN repeat_times INTEGER",
        "run_count": "ALTER TABLE schedules ADD COLUMN run_count INTEGER NOT NULL DEFAULT 0",
        "claim_until": "ALTER TABLE schedules ADD COLUMN claim_until REAL",
    }
    for col, ddl in _sch_alters.items():
        if col not in sch_cols:
            conn.execute(ddl)
    # Backfill state from enabled for pre-harness rows.
    # Freshly-added `state` has DEFAULT 'scheduled' for all existing rows.
    if "state" not in sch_cols and "state" in {
        r[1] for r in conn.execute("PRAGMA table_info(schedules)")
    }:
        conn.execute(
            "UPDATE schedules SET state='paused' WHERE enabled=0 AND state='scheduled'"
        )
    if "schedule_display" not in sch_cols:
        conn.execute(
            "UPDATE schedules SET schedule_display=cron "
            "WHERE (schedule_display IS NULL OR schedule_display='') AND cron != ''"
        )

    from app.runtime.memory.fts import rebuild_knowledge_fts, rebuild_messages_fts

    rebuild_knowledge_fts(conn)
    rebuild_messages_fts(conn)
    conn.commit()
