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
* ``learning_events`` — Companion growth ledger (active-learning reviews)
* ``learning_agent_state`` — Sticky learning counters across restart
* ``swarm_notes`` — Session-scoped shared notes from delegate completes
* ``execution_snippets`` — Lightweight index of execution-lane outcomes
* ``episodic_memories`` — Concrete past experiences (per-user episodic lane)

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
    id                     TEXT PRIMARY KEY,
    name                   TEXT NOT NULL,
    base_url               TEXT NOT NULL DEFAULT '',
    api_key                TEXT NOT NULL DEFAULT '',
    model                  TEXT NOT NULL DEFAULT '',
    reasoning_efforts_json TEXT NOT NULL DEFAULT '[]',
    auth_mode              TEXT NOT NULL DEFAULT 'api_key',
    subscription_provider  TEXT NOT NULL DEFAULT '',
    access_token           TEXT NOT NULL DEFAULT '',
    refresh_token          TEXT NOT NULL DEFAULT '',
    token_expires_at       REAL NOT NULL DEFAULT 0,
    enabled                INTEGER NOT NULL DEFAULT 1,
    created_at             REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    coordinator_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    user_id        TEXT NOT NULL DEFAULT 'web',
    title          TEXT NOT NULL DEFAULT '',
    message_count  INTEGER NOT NULL DEFAULT 0,
    workplace_id   TEXT NOT NULL DEFAULT '',
    reasoning_effort TEXT NOT NULL DEFAULT '',
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
    enabled                INTEGER NOT NULL DEFAULT 1,
    created_at             REAL NOT NULL DEFAULT 0,
    updated_at             REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS knowledge_entries (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL DEFAULT '',
    tags_json      TEXT NOT NULL DEFAULT '[]',
    confidence     REAL NOT NULL DEFAULT 0.7,
    use_count      INTEGER NOT NULL DEFAULT 0,
    success_count  INTEGER NOT NULL DEFAULT 0,
    user_id        TEXT NOT NULL DEFAULT 'web',
    created_at     REAL NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS artifact_shares (
    token      TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    filename   TEXT NOT NULL,
    created_at REAL NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_artifact_shares_session_filename
    ON artifact_shares(session_id, filename);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, key)
);

CREATE TABLE IF NOT EXISTS learning_events (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    agent_id        TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    user_id         TEXT NOT NULL DEFAULT 'web',
    reason          TEXT NOT NULL DEFAULT '',
    review_memory   INTEGER NOT NULL DEFAULT 0,
    review_skills   INTEGER NOT NULL DEFAULT 0,
    saved           INTEGER NOT NULL DEFAULT 0,
    actions_json    TEXT NOT NULL DEFAULT '[]',
    diary           TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    plan_json       TEXT NOT NULL DEFAULT '{}',
    extract_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_learning_events_created
    ON learning_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_events_agent
    ON learning_events(agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS learning_agent_state (
    agent_id              TEXT PRIMARY KEY,
    turns_since_memory    INTEGER NOT NULL DEFAULT 0,
    iters_since_skill     INTEGER NOT NULL DEFAULT 0,
    memory_due            INTEGER NOT NULL DEFAULT 0,
    skills_due            INTEGER NOT NULL DEFAULT 0,
    skill_refine_pending  INTEGER NOT NULL DEFAULT 0,
    last_review_at        REAL NOT NULL DEFAULT 0,
    reviews_started       INTEGER NOT NULL DEFAULT 0,
    reviews_saved         INTEGER NOT NULL DEFAULT 0,
    skipped_cooldown      INTEGER NOT NULL DEFAULT 0,
    skipped_inflight      INTEGER NOT NULL DEFAULT 0,
    updated_at            REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS swarm_notes (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL DEFAULT '',
    from_agent_id    TEXT NOT NULL DEFAULT '',
    to_agent_id      TEXT NOT NULL DEFAULT '',
    delegate_call_id TEXT NOT NULL DEFAULT '',
    reason           TEXT NOT NULL DEFAULT '',
    content          TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'ok',
    created_at       REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_swarm_notes_session
    ON swarm_notes(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS execution_snippets (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL DEFAULT '',
    agent_id    TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'review',
    ref_id      TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    snippet     TEXT NOT NULL DEFAULT '',
    tags_json   TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_execution_snippets_session
    ON execution_snippets(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_snippets_created
    ON execution_snippets(created_at DESC);

CREATE TABLE IF NOT EXISTS episodic_memories (
    id                  TEXT PRIMARY KEY,
    version             INTEGER NOT NULL DEFAULT 1,
    user_id             TEXT NOT NULL DEFAULT 'web',
    agent_id            TEXT NOT NULL DEFAULT '',
    session_id          TEXT NOT NULL DEFAULT '',
    workplace_id        TEXT NOT NULL DEFAULT '',
    parent_episode_id   TEXT NOT NULL DEFAULT '',
    root_episode_id     TEXT NOT NULL DEFAULT '',
    title               TEXT NOT NULL DEFAULT '',
    trigger_summary     TEXT NOT NULL DEFAULT '',
    objective           TEXT NOT NULL DEFAULT '',
    context_summary     TEXT NOT NULL DEFAULT '',
    trajectory_summary  TEXT NOT NULL DEFAULT '',
    outcome_status      TEXT NOT NULL DEFAULT 'unknown',
    outcome_summary     TEXT NOT NULL DEFAULT '',
    reflection_summary  TEXT NOT NULL DEFAULT '',
    importance          REAL NOT NULL DEFAULT 0.5,
    confidence          REAL NOT NULL DEFAULT 0.5,
    utility             REAL NOT NULL DEFAULT 0.5,
    success_score       REAL NOT NULL DEFAULT 0.5,
    memory_score        REAL NOT NULL DEFAULT 0.5,
    state               TEXT NOT NULL DEFAULT 'active',
    started_at          REAL NOT NULL DEFAULT 0,
    ended_at            REAL NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL DEFAULT 0,
    last_accessed_at    REAL NOT NULL DEFAULT 0,
    access_count        INTEGER NOT NULL DEFAULT 0,
    payload_json        TEXT NOT NULL DEFAULT '{}',
    embed_text          TEXT NOT NULL DEFAULT '',
    content             TEXT NOT NULL DEFAULT '',
    content_hash        TEXT NOT NULL DEFAULT '',
    superseded_by       TEXT NOT NULL DEFAULT '',
    reuse_success       INTEGER NOT NULL DEFAULT 0,
    reuse_fail          INTEGER NOT NULL DEFAULT 0,
    decay_score         REAL NOT NULL DEFAULT 1.0,
    entities_json       TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_episodic_user_created
    ON episodic_memories(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_session
    ON episodic_memories(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_state_score
    ON episodic_memories(user_id, state, memory_score DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_content_hash
    ON episodic_memories(user_id, content_hash);

CREATE TABLE IF NOT EXISTS episodic_events (
    id            TEXT PRIMARY KEY,
    episode_id    TEXT NOT NULL,
    sequence      INTEGER NOT NULL DEFAULT 0,
    ts            REAL NOT NULL DEFAULT 0,
    type          TEXT NOT NULL DEFAULT 'observation',
    actor_type    TEXT NOT NULL DEFAULT '',
    actor_id      TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    input_json    TEXT NOT NULL DEFAULT '{}',
    output_json   TEXT NOT NULL DEFAULT '{}',
    result        TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (episode_id) REFERENCES episodic_memories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_episodic_events_episode
    ON episodic_events(episode_id, sequence);

CREATE TABLE IF NOT EXISTS episodic_relations (
    id               TEXT PRIMARY KEY,
    from_episode_id  TEXT NOT NULL,
    to_episode_id    TEXT NOT NULL,
    relation         TEXT NOT NULL DEFAULT 'related',
    weight           REAL NOT NULL DEFAULT 1.0,
    created_at       REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_episodic_rel_from
    ON episodic_relations(from_episode_id, relation);
CREATE INDEX IF NOT EXISTS idx_episodic_rel_to
    ON episodic_relations(to_episode_id, relation);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    transport           TEXT NOT NULL,
    command             TEXT NOT NULL DEFAULT '',
    args_json           TEXT NOT NULL DEFAULT '[]',
    url                 TEXT NOT NULL DEFAULT '',
    env_ciphertext      TEXT NOT NULL DEFAULT '',
    headers_ciphertext  TEXT NOT NULL DEFAULT '',
    enabled             INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'unknown',
    status_message      TEXT NOT NULL DEFAULT '',
    server_info_json    TEXT NOT NULL DEFAULT '{}',
    capabilities_json   TEXT NOT NULL DEFAULT '{}',
    last_connected_at   REAL NOT NULL DEFAULT 0,
    last_discovered_at  REAL NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL DEFAULT 0,
    updated_at          REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mcp_items (
    id             TEXT PRIMARY KEY,
    server_id      TEXT NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    runtime_id     TEXT NOT NULL DEFAULT '',
    name           TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    uri            TEXT NOT NULL DEFAULT '',
    mime_type      TEXT NOT NULL DEFAULT '',
    schema_json    TEXT NOT NULL DEFAULT '{}',
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL DEFAULT 0,
    UNIQUE (server_id, kind, name, uri)
);

CREATE INDEX IF NOT EXISTS idx_mcp_items_server_kind
    ON mcp_items(server_id, kind, enabled);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_items_runtime_id
    ON mcp_items(runtime_id) WHERE runtime_id <> '';
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
    if "reasoning_effort" not in sess_cols:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT ''"
        )
    profile_cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_profiles)")}
    if "reasoning_efforts_json" not in profile_cols:
        conn.execute(
            "ALTER TABLE llm_profiles "
            "ADD COLUMN reasoning_efforts_json TEXT NOT NULL DEFAULT '[]'"
        )
    _profile_alters = {
        "auth_mode": "ALTER TABLE llm_profiles ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'api_key'",
        "subscription_provider": "ALTER TABLE llm_profiles ADD COLUMN subscription_provider TEXT NOT NULL DEFAULT ''",
        "access_token": "ALTER TABLE llm_profiles ADD COLUMN access_token TEXT NOT NULL DEFAULT ''",
        "refresh_token": "ALTER TABLE llm_profiles ADD COLUMN refresh_token TEXT NOT NULL DEFAULT ''",
        "token_expires_at": "ALTER TABLE llm_profiles ADD COLUMN token_expires_at REAL NOT NULL DEFAULT 0",
    }
    for _col, _sql in _profile_alters.items():
        if _col not in profile_cols:
            conn.execute(_sql)
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
        "enabled": "ALTER TABLE workplaces ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
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

    # Learning OS: extract_json + sticky counter persistence.
    le_cols = {r[1] for r in conn.execute("PRAGMA table_info(learning_events)")}
    if "extract_json" not in le_cols:
        conn.execute(
            "ALTER TABLE learning_events "
            "ADD COLUMN extract_json TEXT NOT NULL DEFAULT '{}'"
        )
    table_names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "learning_agent_state" not in table_names:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_agent_state (
                agent_id              TEXT PRIMARY KEY,
                turns_since_memory    INTEGER NOT NULL DEFAULT 0,
                iters_since_skill     INTEGER NOT NULL DEFAULT 0,
                memory_due            INTEGER NOT NULL DEFAULT 0,
                skills_due            INTEGER NOT NULL DEFAULT 0,
                skill_refine_pending  INTEGER NOT NULL DEFAULT 0,
                last_review_at        REAL NOT NULL DEFAULT 0,
                reviews_started       INTEGER NOT NULL DEFAULT 0,
                reviews_saved         INTEGER NOT NULL DEFAULT 0,
                skipped_cooldown      INTEGER NOT NULL DEFAULT 0,
                skipped_inflight      INTEGER NOT NULL DEFAULT 0,
                updated_at            REAL NOT NULL DEFAULT 0
            )
            """
        )

    # Slice 2: knowledge confidence / usage counters + per-account owner.
    kb_cols = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_entries)")}
    _kb_alters = {
        "confidence": (
            "ALTER TABLE knowledge_entries "
            "ADD COLUMN confidence REAL NOT NULL DEFAULT 0.7"
        ),
        "use_count": (
            "ALTER TABLE knowledge_entries "
            "ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0"
        ),
        "success_count": (
            "ALTER TABLE knowledge_entries "
            "ADD COLUMN success_count INTEGER NOT NULL DEFAULT 0"
        ),
        "user_id": (
            "ALTER TABLE knowledge_entries "
            "ADD COLUMN user_id TEXT NOT NULL DEFAULT 'web'"
        ),
    }
    for col, ddl in _kb_alters.items():
        if col not in kb_cols:
            conn.execute(ddl)
    # Index for multi-user knowledge list/search.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_entries_user "
        "ON knowledge_entries(user_id, updated_at DESC)"
    )

    table_names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "swarm_notes" not in table_names:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS swarm_notes (
                id               TEXT PRIMARY KEY,
                session_id       TEXT NOT NULL DEFAULT '',
                from_agent_id    TEXT NOT NULL DEFAULT '',
                to_agent_id      TEXT NOT NULL DEFAULT '',
                delegate_call_id TEXT NOT NULL DEFAULT '',
                reason           TEXT NOT NULL DEFAULT '',
                content          TEXT NOT NULL DEFAULT '',
                status           TEXT NOT NULL DEFAULT 'ok',
                created_at       REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_swarm_notes_session "
            "ON swarm_notes(session_id, created_at DESC)"
        )
    if "execution_snippets" not in table_names:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_snippets (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL DEFAULT '',
                agent_id    TEXT NOT NULL DEFAULT '',
                source      TEXT NOT NULL DEFAULT 'review',
                ref_id      TEXT NOT NULL DEFAULT '',
                title       TEXT NOT NULL DEFAULT '',
                snippet     TEXT NOT NULL DEFAULT '',
                tags_json   TEXT NOT NULL DEFAULT '[]',
                created_at  REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_snippets_session "
            "ON execution_snippets(session_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_snippets_created "
            "ON execution_snippets(created_at DESC)"
        )

    # Episodic experiences (concrete past episodes), distinct from learning diary.
    table_names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "episodic_memories" not in table_names:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodic_memories (
                id                  TEXT PRIMARY KEY,
                version             INTEGER NOT NULL DEFAULT 1,
                user_id             TEXT NOT NULL DEFAULT 'web',
                agent_id            TEXT NOT NULL DEFAULT '',
                session_id          TEXT NOT NULL DEFAULT '',
                workplace_id        TEXT NOT NULL DEFAULT '',
                parent_episode_id   TEXT NOT NULL DEFAULT '',
                root_episode_id     TEXT NOT NULL DEFAULT '',
                title               TEXT NOT NULL DEFAULT '',
                trigger_summary     TEXT NOT NULL DEFAULT '',
                objective           TEXT NOT NULL DEFAULT '',
                context_summary     TEXT NOT NULL DEFAULT '',
                trajectory_summary  TEXT NOT NULL DEFAULT '',
                outcome_status      TEXT NOT NULL DEFAULT 'unknown',
                outcome_summary     TEXT NOT NULL DEFAULT '',
                reflection_summary  TEXT NOT NULL DEFAULT '',
                importance          REAL NOT NULL DEFAULT 0.5,
                confidence          REAL NOT NULL DEFAULT 0.5,
                utility             REAL NOT NULL DEFAULT 0.5,
                success_score       REAL NOT NULL DEFAULT 0.5,
                memory_score        REAL NOT NULL DEFAULT 0.5,
                state               TEXT NOT NULL DEFAULT 'active',
                started_at          REAL NOT NULL DEFAULT 0,
                ended_at            REAL NOT NULL DEFAULT 0,
                created_at          REAL NOT NULL DEFAULT 0,
                last_accessed_at    REAL NOT NULL DEFAULT 0,
                access_count        INTEGER NOT NULL DEFAULT 0,
                payload_json        TEXT NOT NULL DEFAULT '{}',
                embed_text          TEXT NOT NULL DEFAULT '',
                content             TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_user_created "
            "ON episodic_memories(user_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_session "
            "ON episodic_memories(session_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_state_score "
            "ON episodic_memories(user_id, state, memory_score DESC)"
        )
    else:
        ep_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(episodic_memories)")
        }
        _ep_alters = {
            "version": "ALTER TABLE episodic_memories ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
            "workplace_id": "ALTER TABLE episodic_memories ADD COLUMN workplace_id TEXT NOT NULL DEFAULT ''",
            "parent_episode_id": "ALTER TABLE episodic_memories ADD COLUMN parent_episode_id TEXT NOT NULL DEFAULT ''",
            "root_episode_id": "ALTER TABLE episodic_memories ADD COLUMN root_episode_id TEXT NOT NULL DEFAULT ''",
            "trigger_summary": "ALTER TABLE episodic_memories ADD COLUMN trigger_summary TEXT NOT NULL DEFAULT ''",
            "objective": "ALTER TABLE episodic_memories ADD COLUMN objective TEXT NOT NULL DEFAULT ''",
            "context_summary": "ALTER TABLE episodic_memories ADD COLUMN context_summary TEXT NOT NULL DEFAULT ''",
            "trajectory_summary": "ALTER TABLE episodic_memories ADD COLUMN trajectory_summary TEXT NOT NULL DEFAULT ''",
            "outcome_status": "ALTER TABLE episodic_memories ADD COLUMN outcome_status TEXT NOT NULL DEFAULT 'unknown'",
            "outcome_summary": "ALTER TABLE episodic_memories ADD COLUMN outcome_summary TEXT NOT NULL DEFAULT ''",
            "reflection_summary": "ALTER TABLE episodic_memories ADD COLUMN reflection_summary TEXT NOT NULL DEFAULT ''",
            "importance": "ALTER TABLE episodic_memories ADD COLUMN importance REAL NOT NULL DEFAULT 0.5",
            "confidence": "ALTER TABLE episodic_memories ADD COLUMN confidence REAL NOT NULL DEFAULT 0.5",
            "utility": "ALTER TABLE episodic_memories ADD COLUMN utility REAL NOT NULL DEFAULT 0.5",
            "success_score": "ALTER TABLE episodic_memories ADD COLUMN success_score REAL NOT NULL DEFAULT 0.5",
            "memory_score": "ALTER TABLE episodic_memories ADD COLUMN memory_score REAL NOT NULL DEFAULT 0.5",
            "state": "ALTER TABLE episodic_memories ADD COLUMN state TEXT NOT NULL DEFAULT 'active'",
            "started_at": "ALTER TABLE episodic_memories ADD COLUMN started_at REAL NOT NULL DEFAULT 0",
            "ended_at": "ALTER TABLE episodic_memories ADD COLUMN ended_at REAL NOT NULL DEFAULT 0",
            "last_accessed_at": "ALTER TABLE episodic_memories ADD COLUMN last_accessed_at REAL NOT NULL DEFAULT 0",
            "access_count": "ALTER TABLE episodic_memories ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
            "payload_json": "ALTER TABLE episodic_memories ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'",
            "embed_text": "ALTER TABLE episodic_memories ADD COLUMN embed_text TEXT NOT NULL DEFAULT ''",
            "content": "ALTER TABLE episodic_memories ADD COLUMN content TEXT NOT NULL DEFAULT ''",
            "content_hash": "ALTER TABLE episodic_memories ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
            "superseded_by": "ALTER TABLE episodic_memories ADD COLUMN superseded_by TEXT NOT NULL DEFAULT ''",
            "reuse_success": "ALTER TABLE episodic_memories ADD COLUMN reuse_success INTEGER NOT NULL DEFAULT 0",
            "reuse_fail": "ALTER TABLE episodic_memories ADD COLUMN reuse_fail INTEGER NOT NULL DEFAULT 0",
            "decay_score": "ALTER TABLE episodic_memories ADD COLUMN decay_score REAL NOT NULL DEFAULT 1.0",
            "entities_json": "ALTER TABLE episodic_memories ADD COLUMN entities_json TEXT NOT NULL DEFAULT '[]'",
        }
        for col, ddl in _ep_alters.items():
            if col not in ep_cols:
                conn.execute(ddl)
        # Backfill embed/content from legacy freeform content if present.
        ep_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(episodic_memories)")
        }
        if "content" in ep_cols and "embed_text" in ep_cols:
            conn.execute(
                """
                UPDATE episodic_memories
                SET embed_text = content
                WHERE COALESCE(embed_text, '') = '' AND COALESCE(content, '') != ''
                """
            )
            conn.execute(
                """
                UPDATE episodic_memories
                SET objective = CASE WHEN COALESCE(objective,'')='' THEN substr(content,1,500) ELSE objective END,
                    outcome_summary = CASE WHEN COALESCE(outcome_summary,'')='' THEN content ELSE outcome_summary END
                WHERE COALESCE(content, '') != ''
                """
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_content_hash "
            "ON episodic_memories(user_id, content_hash)"
        )

    # Trajectory events + inter-episode relations (prod episodic).
    table_names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "episodic_events" not in table_names:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodic_events (
                id            TEXT PRIMARY KEY,
                episode_id    TEXT NOT NULL,
                sequence      INTEGER NOT NULL DEFAULT 0,
                ts            REAL NOT NULL DEFAULT 0,
                type          TEXT NOT NULL DEFAULT 'observation',
                actor_type    TEXT NOT NULL DEFAULT '',
                actor_id      TEXT NOT NULL DEFAULT '',
                description   TEXT NOT NULL DEFAULT '',
                input_json    TEXT NOT NULL DEFAULT '{}',
                output_json   TEXT NOT NULL DEFAULT '{}',
                result        TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_events_episode "
            "ON episodic_events(episode_id, sequence)"
        )
    if "episodic_relations" not in table_names:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodic_relations (
                id               TEXT PRIMARY KEY,
                from_episode_id  TEXT NOT NULL,
                to_episode_id    TEXT NOT NULL,
                relation         TEXT NOT NULL DEFAULT 'related',
                weight           REAL NOT NULL DEFAULT 1.0,
                created_at       REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_rel_from "
            "ON episodic_relations(from_episode_id, relation)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_episodic_rel_to "
            "ON episodic_relations(to_episode_id, relation)"
        )

    from app.runtime.memory.fts import rebuild_knowledge_fts, rebuild_messages_fts

    rebuild_knowledge_fts(conn)
    rebuild_messages_fts(conn)
    conn.commit()
