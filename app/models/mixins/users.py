"""Login accounts — CRUD over the ``users`` table.

Passwords are stored as scrypt hashes (:mod:`app.core.passwords`). Public
views never include ``password_hash``. Bootstrap inserts ``admin`` from
``TOMO_ADMIN_PASSWORD`` when the table is empty.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from typing import Any

from app.core.passwords import MIN_PASSWORD_LEN, hash_password, verify_password

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{2,32}$")


def _now() -> float:
    return time.time()


def _row_to_user(row: sqlite3.Row, *, include_hash: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or "",
        "role": row["role"] or "admin",
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_hash:
        out["password_hash"] = row["password_hash"]
    return out


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets — safe for HTTP/HTML."""
    out = dict(row)
    out.pop("password_hash", None)
    return out


def count_users(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


def count_enabled(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM users WHERE enabled=1").fetchone()
    return int(row["n"]) if row else 0


def list_users(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    return [public_user(_row_to_user(r)) for r in rows]


def get_user(conn: sqlite3.Connection, user_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return public_user(_row_to_user(row)) if row else None


def get_user_by_username(conn: sqlite3.Connection, username: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM users WHERE username=? COLLATE NOCASE",
        (username.strip(),),
    ).fetchone()
    return public_user(_row_to_user(row)) if row else None


def _get_row_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE username=? COLLATE NOCASE",
        (username.strip(),),
    ).fetchone()


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict[str, Any] | None:
    """Return a public user dict if credentials match and the account is enabled."""
    row = _get_row_by_username(conn, username)
    if not row or not bool(row["enabled"]):
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return public_user(_row_to_user(row))


def _new_id(conn: sqlite3.Connection) -> str:
    for _ in range(8):
        candidate = f"usr_{uuid.uuid4().hex[:10]}"
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (candidate,)).fetchone():
            return candidate
    return f"usr_{uuid.uuid4().hex}"


def _validate_username(username: str) -> str:
    u = username.strip()
    if not _USERNAME_RE.match(u):
        raise ValueError(
            "Username must be 2–32 chars: letters, digits, underscore"
        )
    return u


def create_user(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    username = _validate_username(str(data.get("username") or ""))
    password = str(data.get("password") or "")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if _get_row_by_username(conn, username):
        raise ValueError(f"Username already taken: {username}")
    display_name = str(data.get("display_name") or username).strip() or username
    role = str(data.get("role") or "admin").strip() or "admin"
    enabled = 1 if data.get("enabled", True) else 0
    uid = _new_id(conn)
    now = _now()
    pw_hash = hash_password(password)
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role, enabled, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (uid, username, pw_hash, display_name, role, enabled, now, now),
    )
    conn.commit()
    user = get_user(conn, uid)
    assert user is not None
    return user


def update_user(
    conn: sqlite3.Connection, user_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return None

    display_name = row["display_name"]
    enabled = bool(row["enabled"])
    pw_hash = row["password_hash"]

    if "display_name" in data and data["display_name"] is not None:
        display_name = str(data["display_name"]).strip() or row["username"]

    if "enabled" in data and data["enabled"] is not None:
        new_enabled = bool(data["enabled"])
        if enabled and not new_enabled and count_enabled(conn) <= 1:
            raise ValueError("Cannot disable the last enabled account")
        enabled = new_enabled

    if "password" in data and data["password"]:
        password = str(data["password"])
        if len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
        pw_hash = hash_password(password)

    now = _now()
    conn.execute(
        "UPDATE users SET display_name=?, enabled=?, password_hash=?, updated_at=? WHERE id=?",
        (display_name, 1 if enabled else 0, pw_hash, now, user_id),
    )
    conn.commit()
    return get_user(conn, user_id)


def delete_user(conn: sqlite3.Connection, user_id: str) -> bool:
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return False
    if bool(row["enabled"]) and count_enabled(conn) <= 1:
        raise ValueError("Cannot delete the last enabled account")
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    return True


# Bootstrap runs once in prod; tests rebind empty DBs hundreds of times.
# Same password → same usable hash. scrypt is ~60ms here, so cache it.
_bootstrap_hash_cache: dict[str, str] = {}


def ensure_bootstrap_admin(conn: sqlite3.Connection, password: str) -> None:
    """Insert the default ``admin`` account when the users table is empty."""
    if count_users(conn) > 0:
        return
    now = _now()
    # Dev bootstrap may use a short seed password; API create/update still enforce MIN.
    pw = password if password else "tomo"
    pw_hash = _bootstrap_hash_cache.get(pw)
    if pw_hash is None:
        pw_hash = hash_password(pw, allow_short=True)
        _bootstrap_hash_cache[pw] = pw_hash
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role, enabled, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("usr_admin", "admin", pw_hash, "Admin", "admin", 1, now, now),
    )
    conn.commit()
