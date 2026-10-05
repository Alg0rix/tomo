"""Idempotent multi-user access migration, separate from the legacy schema."""
from __future__ import annotations

import sqlite3


def migrate_access(conn: sqlite3.Connection) -> None:
    legacy_sessions = "execution_mode" not in {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for table, columns in {
        "users": {"access_pending": "INTEGER NOT NULL DEFAULT 0"},
        "workplaces": {
            "access_pending": "INTEGER NOT NULL DEFAULT 0",
            "owner_user_id": "TEXT NOT NULL DEFAULT ''",
            "storage_kind": "TEXT NOT NULL DEFAULT 'external'",
            "destination_id": "TEXT NOT NULL DEFAULT 'local'",
        },
        "sessions": {
            "additional_workplace_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "execution_mode": "TEXT NOT NULL DEFAULT 'restricted'",
            "access_generation": "INTEGER NOT NULL DEFAULT 0",
            "access_pending": "INTEGER NOT NULL DEFAULT 0",
        },
        "schedules": {
            "owner_user_id": "TEXT NOT NULL DEFAULT ''",
            "execution_context_json": "TEXT NOT NULL DEFAULT '{}'",
        },
    }.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    conn.execute("UPDATE workplaces SET destination_id=id WHERE kind IN ('ssh','tunnel') AND destination_id='local'")
    # Existing recognized Admin accounts stay Admin. Unknown roles never elevate.
    conn.execute("UPDATE users SET role='member' WHERE role NOT IN ('admin','member') OR role IS NULL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS resource_grants (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            resource_type TEXT NOT NULL CHECK(resource_type IN ('workplace','agent','model','unrestricted')),
            resource_id TEXT NOT NULL,
            permission TEXT NOT NULL CHECK(permission IN ('read','read_write','use')),
            state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','pending')),
            granted_by TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(user_id,resource_type,resource_id)
        );
        CREATE TABLE IF NOT EXISTS user_execution_quotas (
            user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            cpu REAL NOT NULL DEFAULT 2 CHECK(cpu > 0),
            memory_mb INTEGER NOT NULL DEFAULT 2048 CHECK(memory_mb > 0),
            disk_mb INTEGER NOT NULL DEFAULT 4096 CHECK(disk_mb > 0),
            duration_seconds INTEGER NOT NULL DEFAULT 300 CHECK(duration_seconds > 0),
            gpu_allowed INTEGER NOT NULL DEFAULT 0 CHECK(gpu_allowed IN (0,1))
        );
        -- No foreign key: deleting a chat/account must not erase evidence
        -- of a possibly live container. Empty session_id certifies namespace
        -- recovery; missing evidence always requires real runtime teardown.
        CREATE TABLE IF NOT EXISTS container_admissions (
            namespace TEXT NOT NULL,
            session_id TEXT NOT NULL,
            pending INTEGER NOT NULL CHECK(pending IN (0,1)),
            PRIMARY KEY(namespace,session_id)
        );
        CREATE TABLE IF NOT EXISTS access_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT NOT NULL,
            subject_user_id TEXT NOT NULL DEFAULT '',
            resource_type TEXT NOT NULL DEFAULT '',
            permission TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            job_id TEXT NOT NULL DEFAULT '',
            agent_id TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            destination_id TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_access_audit_actor ON access_audit(actor_id,created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_personal_space_owner
            ON workplaces(owner_user_id) WHERE storage_kind='personal';
        CREATE TRIGGER IF NOT EXISTS users_valid_role_insert BEFORE INSERT ON users
        WHEN NEW.role IS NULL OR NEW.role NOT IN ('admin','member') BEGIN
            SELECT RAISE(ABORT,'Invalid account role'); END;
        CREATE TRIGGER IF NOT EXISTS users_valid_role_update BEFORE UPDATE OF role ON users
        WHEN NEW.role IS NULL OR NEW.role NOT IN ('admin','member') BEGIN
            SELECT RAISE(ABORT,'Invalid account role'); END;
        CREATE TRIGGER IF NOT EXISTS users_last_admin_update BEFORE UPDATE OF role,enabled ON users
        WHEN OLD.role='admin' AND OLD.enabled=1 AND (NEW.role<>'admin' OR NEW.enabled<>1)
          AND (SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1)<=1 BEGIN
            SELECT RAISE(ABORT,'Cannot remove the last enabled Admin'); END;
        CREATE TRIGGER IF NOT EXISTS users_last_admin_delete BEFORE DELETE ON users
        WHEN OLD.role='admin' AND OLD.enabled=1
          AND (SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1)<=1 BEGIN
            SELECT RAISE(ABORT,'Cannot remove the last enabled Admin'); END;
    """)
    audit_columns = {row[1] for row in conn.execute("PRAGMA table_info(access_audit)")}
    for column in ("subject_user_id", "resource_type", "permission"):
        if column not in audit_columns:
            conn.execute(f"ALTER TABLE access_audit ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    if legacy_sessions:
        # Existing Admin-owned chats already ran on the host. Preserve that
        # explicit destination privilege only for those existing Admin rows;
        # do not reinterpret Member sessions or future chats as unrestricted.
        conn.execute("INSERT OR IGNORE INTO resource_grants(user_id,resource_type,resource_id,permission,state,granted_by,updated_at) SELECT DISTINCT s.user_id,'unrestricted',s.workplace_id,'use','active',s.user_id,s.updated_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.role='admin' AND u.enabled=1 AND s.workplace_id<>''")
        conn.execute("UPDATE sessions SET execution_mode='unrestricted' WHERE workplace_id<>'' AND user_id IN (SELECT id FROM users WHERE role='admin' AND enabled=1)")
    # Repair the first multi-user release's automatic Admin personal-space
    # default (web and linked Telegram). Generation-zero chats have never had
    # a user access mutation; do not override explicit Restricted choices,
    # revocations or pending teardown. Bump the ceiling so old durable work
    # cannot silently widen from restricted to host execution after restart.
    conn.execute("""
        UPDATE sessions SET execution_mode='unrestricted', access_generation=1
        WHERE execution_mode='restricted' AND access_generation=0 AND access_pending=0
          AND user_id IN (SELECT id FROM users WHERE role='admin' AND enabled=1 AND access_pending=0)
          AND EXISTS (SELECT 1 FROM workplaces w WHERE w.id=sessions.workplace_id
                      AND w.owner_user_id=sessions.user_id AND w.storage_kind='personal'
                      AND w.enabled=1 AND w.access_pending=0)
          AND NOT EXISTS (SELECT 1 FROM access_audit a WHERE a.session_id=sessions.id AND a.action='chat.access')
          AND NOT EXISTS (SELECT 1 FROM resource_grants g WHERE g.user_id=sessions.user_id
                          AND g.resource_type='unrestricted' AND g.resource_id=sessions.workplace_id
                          AND g.state='pending')
          AND (EXISTS (SELECT 1 FROM resource_grants g WHERE g.user_id=sessions.user_id
                       AND g.resource_type='unrestricted' AND g.resource_id=sessions.workplace_id
                       AND g.state='active')
               OR NOT EXISTS (SELECT 1 FROM access_audit a WHERE a.subject_user_id=sessions.user_id
                              AND a.resource_type='unrestricted' AND a.destination_id=sessions.workplace_id
                              AND a.action='grant.revoke'))
    """)
