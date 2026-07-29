"""Chat sessions and the session<->agent membership table.

A session row stores ``coordinator_id`` directly; the full ``agent_ids`` list
is reconstructed from the ordered ``session_agents`` rows. The dict returned
to callers keeps the legacy ``agent_id`` (== coordinator) and ``agent_ids``
fields so the API/UI shapes are unchanged.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any


def _now() -> float:
    return time.time()


def _new_sid() -> str:
    return f"ses_{uuid.uuid4().hex[:8]}"


def session_agent_ids(conn: sqlite3.Connection, session_id: str) -> list[str]:
    """Stored membership only (may lag behind live enabled agents for swarms)."""
    rows = conn.execute(
        "SELECT agent_id FROM session_agents WHERE session_id=? ORDER BY position",
        (session_id,),
    ).fetchall()
    return [r["agent_id"] for r in rows]


def _stored_is_swarm(stored_ids: list[str]) -> bool:
    """Multi-member rows mean swarm mode; solo (1 id) is intentional single-agent."""
    return len(stored_ids) != 1


def resolve_live_agent_ids(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    coordinator_id: str | None = None,
) -> tuple[list[str], bool]:
    """Return ``(agent_ids, is_swarm)`` with live enabled agents for swarm chats.

    Swarm sessions always see every currently enabled agent so a newly created
    or re-enabled agent is available on the next turn without editing the
    session. Solo sessions (exactly one stored member) stay fixed.
    """
    from app.models.mixins.agents import list_enabled_agent_ids

    stored = session_agent_ids(conn, session_id)
    if not _stored_is_swarm(stored):
        return list(stored), False

    enabled = list_enabled_agent_ids(conn)
    if not enabled:
        return list(stored), True

    coord = (coordinator_id or "").strip()
    if not coord or coord not in enabled:
        # Prefer stored coordinator if still enabled; else first enabled.
        row = conn.execute(
            "SELECT coordinator_id FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        stored_coord = (row["coordinator_id"] if row else None) or ""
        if stored_coord in enabled:
            coord = stored_coord
        elif stored and stored[0] in enabled:
            coord = stored[0]
        else:
            coord = enabled[0]

    ordered: list[str] = [coord]
    for aid in enabled:
        if aid not in ordered:
            ordered.append(aid)
    return ordered, True


def sync_swarm_membership(conn: sqlite3.Connection, session_id: str) -> list[str]:
    """Rewrite ``session_agents`` to the live swarm when the session is multi-member.

    Returns the resolved agent id list. No-op for solo sessions.
    """
    row = conn.execute(
        "SELECT coordinator_id FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    if not row:
        return []
    live, is_swarm = resolve_live_agent_ids(
        conn, session_id, coordinator_id=row["coordinator_id"]
    )
    if not is_swarm:
        return live
    stored = session_agent_ids(conn, session_id)
    if stored == live:
        return live
    conn.execute("DELETE FROM session_agents WHERE session_id=?", (session_id,))
    conn.executemany(
        "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
        [(session_id, aid, pos) for pos, aid in enumerate(live)],
    )
    # Keep coordinator pointer valid.
    coord = live[0] if live else row["coordinator_id"]
    if coord and coord != row["coordinator_id"]:
        conn.execute(
            "UPDATE sessions SET coordinator_id=? WHERE id=?",
            (coord, session_id),
        )
    conn.commit()
    return live


def _session_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    ids, is_swarm = resolve_live_agent_ids(
        conn, row["id"], coordinator_id=row["coordinator_id"]
    )
    coord = row["coordinator_id"] or (ids[0] if ids else None)
    # workplace_id may be missing on very old rows before migrate runs.
    try:
        workplace_id = (row["workplace_id"] or "").strip()
    except (IndexError, KeyError):
        workplace_id = ""
    return {
        "id": row["id"],
        "agent_id": coord,
        "agent_ids": ids,
        "coordinator_id": coord,
        "is_swarm": is_swarm,
        "user_id": row["user_id"],
        "title": row["title"],
        "message_count": row["message_count"],
        "workplace_id": workplace_id,
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
    }


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return _session_to_dict(conn, row) if row else None


def list_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
    return [_session_to_dict(conn, r) for r in rows]


def _valid_agent_ids(conn: sqlite3.Connection, agent_ids: list[str]) -> list[str]:
    ids: list[str] = []
    for aid in agent_ids:
        if aid in ids:
            continue
        if conn.execute("SELECT 1 FROM agents WHERE id=?", (aid,)).fetchone():
            ids.append(aid)
    return ids


def create_swarm_session(
    conn: sqlite3.Connection,
    agent_ids: list[str] | None = None,
    user_id: str = "web",
    coordinator_id: str | None = None,
    workplace_id: str | None = None,
) -> str:
    """Create a session.

    Empty / omitted ``agent_ids`` means **full swarm** (all enabled agents).
    Pass a single id for intentional one-agent chat.

    ``workplace_id`` is the chat's default workplace (prefer **local** for
    folder context). Tunnel/SSH remain reachable via agents that own them
    or ``workplace=`` on tools.
    """
    from app.models.mixins.agents import list_enabled_agent_ids

    raw = list(agent_ids) if agent_ids is not None else []
    if not raw:
        # Empty list / omitted → full swarm (all enabled agents).
        raw = list_enabled_agent_ids(conn)
    ids = _valid_agent_ids(conn, raw)
    if not ids:
        raise ValueError("At least one valid agent is required")
    coord = coordinator_id if coordinator_id in ids else ids[0]
    super_row = conn.execute(
        "SELECT id FROM agents WHERE is_super=1 AND id IN (%s)" % ",".join("?" * len(ids)),
        ids,
    ).fetchone()
    if super_row:
        coord = super_row["id"]
    # Coordinator first in membership for stable UI ordering.
    if coord in ids:
        ids = [coord] + [a for a in ids if a != coord]
    wid = (workplace_id or "").strip()
    if wid:
        row = conn.execute(
            "SELECT id FROM workplaces WHERE id=?", (wid,)
        ).fetchone()
        if not row:
            raise ValueError(f"Workplace not found: {wid}")
    sid = _new_sid()
    now = _now()
    title = "New swarm chat" if len(ids) > 1 else "New conversation"
    conn.execute(
        "INSERT INTO sessions (id, coordinator_id, user_id, title, message_count, "
        "workplace_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (sid, coord, user_id, title, 0, wid, now, now),
    )
    conn.executemany(
        "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
        [(sid, aid, pos) for pos, aid in enumerate(ids)],
    )
    conn.commit()
    return sid


def set_session_workplace(
    conn: sqlite3.Connection, session_id: str, workplace_id: str | None
) -> dict[str, Any] | None:
    """Set or clear the session's default workplace."""
    row = conn.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    wid = (workplace_id or "").strip()
    if wid:
        wp = conn.execute(
            "SELECT id FROM workplaces WHERE id=?", (wid,)
        ).fetchone()
        if not wp:
            raise ValueError(f"Workplace not found: {wid}")
    conn.execute(
        "UPDATE sessions SET workplace_id=?, updated_at=? WHERE id=?",
        (wid, _now(), session_id),
    )
    conn.commit()
    return get_session(conn, session_id)


def update_session_agents(
    conn: sqlite3.Connection, session_id: str, agent_ids: list[str]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    ids = _valid_agent_ids(conn, agent_ids)
    if not ids:
        raise ValueError("At least one valid agent is required")
    conn.execute("DELETE FROM session_agents WHERE session_id=?", (session_id,))
    conn.executemany(
        "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
        [(session_id, aid, pos) for pos, aid in enumerate(ids)],
    )
    coord = row["coordinator_id"] if row["coordinator_id"] in ids else ids[0]
    conn.execute(
        "UPDATE sessions SET coordinator_id=?, updated_at=? WHERE id=?",
        (coord, _now(), session_id),
    )
    conn.commit()
    return get_session(conn, session_id)


def set_session_title(
    conn: sqlite3.Connection, session_id: str, title: str
) -> dict[str, Any] | None:
    """Set session title and bump ``updated_at``. Returns the session dict or None."""
    row = conn.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
        (title, _now(), session_id),
    )
    conn.commit()
    return get_session(conn, session_id)


def find_session(conn: sqlite3.Connection, agent_id: str, user_id: str) -> str | None:
    """Return the most recent single-agent session id for (agent_id, user_id), or None.

    Looks up — never creates — so callers can no-op when no session exists.
    """
    row = conn.execute(
        "SELECT s.id FROM sessions s "
        "WHERE s.user_id=? AND s.coordinator_id=? "
        "AND (SELECT COUNT(*) FROM session_agents sa WHERE sa.session_id=s.id)=1 "
        "AND EXISTS (SELECT 1 FROM session_agents sa WHERE sa.session_id=s.id AND sa.agent_id=?) "
        "ORDER BY s.updated_at DESC LIMIT 1",
        (user_id, agent_id, agent_id),
    ).fetchone()
    return row["id"] if row else None


def get_or_create_session(conn: sqlite3.Connection, agent_id: str, user_id: str) -> str:
    """Return the most recent single-agent session for (agent_id, user_id), or create one.

    Raises ``ValueError`` if ``agent_id`` does not exist, mirroring
    :func:`create_swarm_session`'s validation — otherwise the inserts would
    raise a raw ``IntegrityError`` on the ``coordinator_id``/``agent_id`` FKs.
    """
    if not conn.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
        raise ValueError(f"Agent does not exist: {agent_id}")
    existing = find_session(conn, agent_id, user_id)
    if existing:
        return existing
    sid = _new_sid()
    now = _now()
    conn.execute(
        "INSERT INTO sessions (id, coordinator_id, user_id, title, message_count, "
        "workplace_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (sid, agent_id, user_id, "New conversation", 0, "", now, now),
    )
    conn.execute(
        "INSERT INTO session_agents (session_id, agent_id, position) VALUES (?,?,?)",
        (sid, agent_id, 0),
    )
    conn.commit()
    return sid


# Default titles used for never-messaged drafts. Cleared chats keep a custom
# title, so pruning by these titles + message_count=0 only removes unused drafts.
_DRAFT_TITLES = frozenset({"New conversation", "New swarm chat"})


def delete_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """Delete a session (messages + membership cascade). Returns True if removed."""
    row = conn.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    conn.commit()
    return True


def prune_empty_draft_sessions(
    conn: sqlite3.Connection, *, keep_id: str | None = None
) -> list[str]:
    """Delete never-messaged draft sessions (default title + message_count=0).

    Skips ``keep_id`` so an open draft is not removed mid-compose. Returns
    deleted session ids.
    """
    rows = conn.execute(
        "SELECT id FROM sessions WHERE message_count=0 AND title IN (?,?)",
        tuple(_DRAFT_TITLES),
    ).fetchall()
    deleted: list[str] = []
    for row in rows:
        sid = row["id"]
        if keep_id and sid == keep_id:
            continue
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
        deleted.append(sid)
    if deleted:
        conn.commit()
    return deleted

