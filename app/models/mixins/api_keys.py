"""Per-account API keys for Bearer / X-API-Key access to ``/api/*``.

Plaintext token is shown **once** on create. Only SHA-256 hashes are stored.
Token format: ``tomo_<urlsafe>``.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import uuid
from typing import Any

_KEY_PREFIX = "tomo_"


def _now() -> float:
    return time.time()


def _hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["name"] or "",
        "key_prefix": row["key_prefix"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
    }


def list_api_keys(
    conn: sqlite3.Connection, user_id: str | None = None
) -> list[dict[str, Any]]:
    if user_id:
        rows = conn.execute(
            "SELECT * FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
    return [_row_public(r) for r in rows]


def get_api_key(conn: sqlite3.Connection, key_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
    return _row_public(row) if row else None


def create_api_key(
    conn: sqlite3.Connection, user_id: str, name: str = ""
) -> dict[str, Any]:
    """Create a key. Return public fields plus one-time ``token`` plaintext."""
    user = conn.execute(
        "SELECT id, username, enabled FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not user:
        raise ValueError("User not found")
    if not bool(user["enabled"]):
        raise ValueError("Cannot create API key for a disabled account")

    token = _KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = _hash_key(token)
    key_prefix = token[:12] + "…"
    kid = f"key_{uuid.uuid4().hex[:12]}"
    now = _now()
    label = (name or "").strip() or "API key"
    conn.execute(
        "INSERT INTO api_keys (id, user_id, name, key_prefix, key_hash, created_at, "
        "last_used_at) VALUES (?,?,?,?,?,?,NULL)",
        (kid, user_id, label, key_prefix, key_hash, now),
    )
    conn.commit()
    out = get_api_key(conn, kid)
    assert out is not None
    out["token"] = token
    out["username"] = user["username"]
    return out


def delete_api_key(conn: sqlite3.Connection, key_id: str) -> bool:
    cur = conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    conn.commit()
    return cur.rowcount > 0


def authenticate_api_key(
    conn: sqlite3.Connection, token: str
) -> dict[str, Any] | None:
    """Return ``{user_id, username, key_id}`` if the token is valid and user enabled."""
    raw = (token or "").strip()
    if not raw.startswith(_KEY_PREFIX) or len(raw) < 20:
        return None
    key_hash = _hash_key(raw)
    row = conn.execute(
        "SELECT k.id AS key_id, k.user_id, u.username, u.enabled "
        "FROM api_keys k JOIN users u ON u.id = k.user_id "
        "WHERE k.key_hash=?",
        (key_hash,),
    ).fetchone()
    if not row or not bool(row["enabled"]):
        return None
    now = _now()
    conn.execute(
        "UPDATE api_keys SET last_used_at=? WHERE id=?",
        (now, row["key_id"]),
    )
    conn.commit()
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "key_id": row["key_id"],
    }
