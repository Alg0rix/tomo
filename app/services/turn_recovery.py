"""Durable in-flight chat requests; execution resumes from existing history."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .store import store


RESUME_PROMPT = (
    "Tomo restarted while working on this request. Continue the unfinished work "
    "from the conversation and recorded tool results. Do not start over or repeat "
    "completed actions. A tool call without a recorded result has an unknown "
    "outcome: inspect the current state before retrying any action, and ask the "
    "user if its outcome cannot be verified. Any pending approval or clarification "
    "expired during restart; request it again if still needed. Original request:\n"
)


def save_request(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = uuid4().hex

    def save(conn):
        history_start = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM messages WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO session_turns (session_id, token, payload_json, history_start) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
            "token=excluded.token, payload_json=excluded.payload_json, "
            "history_start=excluded.history_start, completed=0, reply=''",
            (session_id, token, json.dumps(payload), history_start),
        )
        conn.commit()
        return {
            **payload,
            "session_id": session_id,
            "token": token,
            "history_start": history_start,
            "completed": False,
            "reply": "",
        }

    return store.with_db(save)


def pending_requests() -> list[dict[str, Any]]:
    def read(conn):
        rows = conn.execute("SELECT * FROM session_turns").fetchall()
        return [
            {
                **json.loads(row["payload_json"]),
                "session_id": row["session_id"],
                "token": row["token"],
                "history_start": row["history_start"],
                "completed": bool(row["completed"]),
                "reply": row["reply"],
            }
            for row in rows
        ]

    return store.with_db(read)


def history_since(request: dict[str, Any]) -> list[dict[str, Any]]:
    # Use message IDs, not timestamps/counts: append and its ID commit together.
    from app.models.mixins.messages import _row_to_entry

    return store.with_db(
        lambda conn: [
            _row_to_entry(row)
            for row in conn.execute(
                "SELECT * FROM messages WHERE session_id=? AND id>? ORDER BY id",
                (request["session_id"], request["history_start"]),
            ).fetchall()
        ]
    )


def finish_request(request: dict[str, Any]) -> None:
    def finish(conn):
        conn.execute(
            "DELETE FROM session_turns WHERE session_id=? AND token=?",
            (request["session_id"], request["token"]),
        )
        conn.commit()

    store.with_db(finish)


def complete_request(request: dict[str, Any], reply: str) -> None:
    def complete(conn):
        conn.execute(
            "UPDATE session_turns SET completed=1, reply=? WHERE session_id=? AND token=?",
            (reply, request["session_id"], request["token"]),
        )
        conn.commit()

    store.with_db(complete)


def acknowledge_delivery(session_id: str) -> None:
    """Clear only completed Telegram work after its answer was delivered."""

    def acknowledge(conn):
        conn.execute(
            "DELETE FROM session_turns WHERE session_id=? AND completed=1",
            (session_id,),
        )
        conn.commit()

    store.with_db(acknowledge)


def cancel_request(session_id: str) -> bool:
    def cancel(conn):
        result = conn.execute(
            "DELETE FROM session_turns WHERE session_id=?", (session_id,)
        )
        conn.commit()
        return result.rowcount > 0

    return store.with_db(cancel)
