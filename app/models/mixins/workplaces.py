"""Workplaces — CRUD + Connect + Tomo Connector pairing.

Kinds:

* ``local`` — path on disk; Connect checks the directory exists.
* ``ssh`` — remote host; password/key stored as Fernet ciphertext.
* ``tunnel`` — Tomo Connector (outbound WebSocket). Status is only
  ``connected`` while a live socket is registered on the hub. Pairing codes
  and long-lived connector tokens are stored here (token encrypted at rest).

Public views never expose decrypted secrets; blank password/key/token on
update keeps the existing ciphertext.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from typing import Any

from app.core.secrets import decrypt_secret, encrypt_secret
from app.workplaces.pairing import (
    generate_pairing_code,
    pairing_expires_at,
    pairing_ttl_seconds,
)

_KINDS = frozenset({"local", "ssh", "tunnel"})
_STATUSES = frozenset({"offline", "connected", "later", "pairing"})


def _now() -> float:
    return time.time()


def _agent_count(conn: sqlite3.Connection, workplace_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM agents WHERE workplace_id=?",
        (workplace_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def _row_to_workplace(row: sqlite3.Row, agent_count: int) -> dict[str, Any]:
    keys = set(row.keys())

    def col(name: str, default: Any = "") -> Any:
        return row[name] if name in keys else default

    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "host": row["host"],
        "root_path": row["root_path"],
        "ssh_host": row["ssh_host"],
        "ssh_port": int(row["ssh_port"] or 22),
        "ssh_user": row["ssh_user"],
        "ssh_password": row["ssh_password"],
        "ssh_key": row["ssh_key"],
        "pairing_code": col("pairing_code", "") or "",
        "pairing_expires_at": float(col("pairing_expires_at", 0) or 0),
        "connector_token": col("connector_token", "") or "",
        "connector_last_seen_at": float(col("connector_last_seen_at", 0) or 0),
        "connector_version": col("connector_version", "") or "",
        "connector_hostname": col("connector_hostname", "") or "",
        "agent_count": agent_count,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def public_workplace(row: dict[str, Any]) -> dict[str, Any]:
    """Drop ciphertext; add set flags — safe for HTTP/HTML."""
    out = dict(row)
    pwd = decrypt_secret(str(out.pop("ssh_password", "") or ""))
    key = decrypt_secret(str(out.pop("ssh_key", "") or ""))
    token = decrypt_secret(str(out.pop("connector_token", "") or ""))
    out["password_set"] = bool(pwd)
    out["key_set"] = bool(key)
    out["connector_token_set"] = bool(token)
    # Pairing code is short-lived and meant to be shown once for install;
    # expire it in the public view if past TTL.
    code = (out.get("pairing_code") or "").strip()
    exp = float(out.get("pairing_expires_at") or 0)
    if code and exp and exp < _now():
        out["pairing_code"] = ""
        out["pairing_expired"] = True
    else:
        out["pairing_expired"] = bool(code and exp and exp < _now())
    out["pairing_ttl_seconds"] = pairing_ttl_seconds()
    return out


def _display_host(data: dict[str, Any], kind: str) -> str:
    """Derive the tile/detail ``host`` string from kind-specific fields."""
    explicit = (data.get("host") or "").strip()
    if explicit:
        return explicit
    if kind == "local":
        return (data.get("root_path") or "").strip() or "local"
    if kind == "ssh":
        user = (data.get("ssh_user") or "").strip()
        host = (data.get("ssh_host") or "").strip()
        if user and host:
            return f"{user}@{host}"
        return host or user or "ssh"
    if kind == "tunnel":
        hostname = (data.get("connector_hostname") or "").strip()
        if hostname:
            return hostname
        return (data.get("ssh_host") or data.get("root_path") or "").strip() or "tunnel"
    return ""


def list_workplaces(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM workplaces ORDER BY created_at ASC"
    ).fetchall()
    return [
        public_workplace(_row_to_workplace(r, _agent_count(conn, r["id"])))
        for r in rows
    ]


def get_workplace(conn: sqlite3.Connection, workplace_id: str) -> dict[str, Any] | None:
    """Public (no secrets) workplace, or ``None``."""
    row = conn.execute(
        "SELECT * FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    if not row:
        return None
    return public_workplace(_row_to_workplace(row, _agent_count(conn, workplace_id)))


def get_workplace_secrets(
    conn: sqlite3.Connection, workplace_id: str
) -> dict[str, Any] | None:
    """Runtime view with **decrypted** SSH password/key (Connect / remotes only)."""
    row = conn.execute(
        "SELECT * FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    if not row:
        return None
    wp = _row_to_workplace(row, _agent_count(conn, workplace_id))
    wp["ssh_password"] = decrypt_secret(str(wp.get("ssh_password") or ""))
    wp["ssh_key"] = decrypt_secret(str(wp.get("ssh_key") or ""))
    wp["connector_token"] = decrypt_secret(str(wp.get("connector_token") or ""))
    return wp


def create_workplace(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    wid = data["id"]
    if conn.execute("SELECT 1 FROM workplaces WHERE id=?", (wid,)).fetchone():
        raise ValueError("Workplace ID already exists")
    kind = (data.get("kind") or "local").strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"Invalid workplace kind: {kind}")
    # Tunnel starts offline until a connector pairs; local/ssh offline until Connect.
    status = "offline" if kind == "tunnel" else "offline"
    now = _now()
    host = _display_host(data, kind)
    pairing_code = ""
    pairing_exp = 0.0
    if kind == "tunnel":
        pairing_code = generate_pairing_code()
        pairing_exp = pairing_expires_at(now)
        status = "pairing"
    conn.execute(
        "INSERT INTO workplaces (id, name, kind, status, host, root_path, "
        "ssh_host, ssh_port, ssh_user, ssh_password, ssh_key, "
        "pairing_code, pairing_expires_at, connector_token, "
        "connector_last_seen_at, connector_version, connector_hostname, "
        "created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            wid,
            data.get("name") or wid,
            kind,
            status,
            host,
            (data.get("root_path") or "").strip(),
            (data.get("ssh_host") or "").strip(),
            int(data.get("ssh_port") or 22),
            (data.get("ssh_user") or "").strip(),
            encrypt_secret(data.get("ssh_password")),
            encrypt_secret(data.get("ssh_key")),
            pairing_code,
            pairing_exp,
            "",
            0.0,
            "",
            "",
            now,
            now,
        ),
    )
    conn.commit()
    return get_workplace(conn, wid)  # type: ignore[return-value]


def update_workplace(
    conn: sqlite3.Connection, workplace_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    if not row:
        return None
    sets: list[str] = []
    params: list[Any] = []
    kind = row["kind"]
    if "kind" in data and data["kind"] is not None:
        new_kind = str(data["kind"]).strip().lower()
        if new_kind not in _KINDS:
            raise ValueError(f"Invalid workplace kind: {new_kind}")
        kind = new_kind
        sets.append("kind=?")
        params.append(kind)
        if kind == "tunnel" and row["kind"] != "tunnel":
            # Fresh tunnel: issue pairing, leave offline until connect.
            code = generate_pairing_code()
            sets.append("status=?")
            params.append("pairing")
            sets.append("pairing_code=?")
            params.append(code)
            sets.append("pairing_expires_at=?")
            params.append(pairing_expires_at())
    for key in ("name", "root_path", "ssh_host", "ssh_user"):
        if key in data and data[key] is not None:
            sets.append(f"{key}=?")
            params.append(str(data[key]).strip() if isinstance(data[key], str) else data[key])
    if "ssh_port" in data and data["ssh_port"] is not None:
        sets.append("ssh_port=?")
        params.append(int(data["ssh_port"]))
    if "ssh_password" in data:
        incoming = data["ssh_password"]
        if incoming is not None and str(incoming).strip():
            sets.append("ssh_password=?")
            params.append(encrypt_secret(str(incoming)))
    if "ssh_key" in data:
        incoming = data["ssh_key"]
        if incoming is not None and str(incoming).strip():
            sets.append("ssh_key=?")
            params.append(encrypt_secret(str(incoming)))
    if "host" in data and data["host"] is not None:
        sets.append("host=?")
        params.append(str(data["host"]).strip())
    elif any(k in data for k in ("root_path", "ssh_host", "ssh_user", "kind")):
        merged = {
            "host": "",
            "root_path": data.get("root_path", row["root_path"]),
            "ssh_host": data.get("ssh_host", row["ssh_host"]),
            "ssh_user": data.get("ssh_user", row["ssh_user"]),
            "connector_hostname": (
                row["connector_hostname"]
                if "connector_hostname" in row.keys()
                else ""
            ),
        }
        sets.append("host=?")
        params.append(_display_host(merged, kind))
    if "status" in data and data["status"] is not None:
        st = str(data["status"]).strip().lower()
        if st in _STATUSES:
            # API cannot force tunnel to connected without a live socket.
            if kind == "tunnel" and st == "connected":
                st = "offline"
            sets.append("status=?")
            params.append(st)
    if sets:
        sets.append("updated_at=?")
        params.append(_now())
        params.append(workplace_id)
        conn.execute(
            f"UPDATE workplaces SET {', '.join(sets)} WHERE id=?", params
        )
        conn.commit()
    return get_workplace(conn, workplace_id)


def delete_workplace(conn: sqlite3.Connection, workplace_id: str) -> bool:
    if not conn.execute(
        "SELECT 1 FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone():
        return False
    conn.execute(
        "UPDATE agents SET workplace_id='' WHERE workplace_id=?",
        (workplace_id,),
    )
    conn.execute("DELETE FROM workplaces WHERE id=?", (workplace_id,))
    conn.commit()
    return True


def set_status(
    conn: sqlite3.Connection,
    workplace_id: str,
    status: str,
    *,
    allow_connected: bool = False,
) -> dict[str, Any] | None:
    """Persist status. Tunnel ``connected`` only when ``allow_connected`` (hub)."""
    st = status.strip().lower()
    if st not in _STATUSES:
        return get_workplace(conn, workplace_id)
    row = conn.execute(
        "SELECT kind FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    if not row:
        return None
    if row["kind"] == "tunnel" and st == "connected" and not allow_connected:
        st = "offline"
    conn.execute(
        "UPDATE workplaces SET status=?, updated_at=? WHERE id=?",
        (st, _now(), workplace_id),
    )
    conn.commit()
    return get_workplace(conn, workplace_id)


def issue_pairing_code(
    conn: sqlite3.Connection, workplace_id: str
) -> dict[str, Any] | None:
    """Generate a fresh pairing code (TTL). Status → ``pairing`` if offline."""
    row = conn.execute(
        "SELECT kind, status FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    if not row:
        return None
    if row["kind"] != "tunnel":
        raise ValueError("Pairing codes are only for tunnel workplaces")
    code = generate_pairing_code()
    exp = pairing_expires_at()
    now = _now()
    status = row["status"]
    # Keep connected if already live; otherwise mark pairing.
    if status != "connected":
        status = "pairing"
    conn.execute(
        "UPDATE workplaces SET pairing_code=?, pairing_expires_at=?, "
        "status=?, updated_at=? WHERE id=?",
        (code, exp, status, now, workplace_id),
    )
    conn.commit()
    return get_workplace(conn, workplace_id)


def find_by_pairing_code(
    conn: sqlite3.Connection, code: str
) -> dict[str, Any] | None:
    """Lookup tunnel workplace by active (non-expired) pairing code."""
    raw = (code or "").strip().upper()
    if not raw:
        return None
    now = _now()
    row = conn.execute(
        "SELECT * FROM workplaces WHERE kind='tunnel' AND upper(pairing_code)=? "
        "AND pairing_expires_at > ?",
        (raw, now),
    ).fetchone()
    if not row:
        return None
    return _row_to_workplace(row, _agent_count(conn, row["id"]))


def find_by_connector_token(
    conn: sqlite3.Connection, token: str
) -> dict[str, Any] | None:
    """Lookup tunnel workplace by decrypted connector token match."""
    raw = (token or "").strip()
    if not raw:
        return None
    rows = conn.execute(
        "SELECT * FROM workplaces WHERE kind='tunnel' AND connector_token != ''"
    ).fetchall()
    for row in rows:
        plain = decrypt_secret(str(row["connector_token"] or ""))
        if plain and secrets.compare_digest(plain, raw):
            return _row_to_workplace(row, _agent_count(conn, row["id"]))
    return None


def complete_pairing(
    conn: sqlite3.Connection,
    workplace_id: str,
    *,
    hostname: str = "",
    version: str = "",
    rotate_token: bool = True,
) -> str:
    """Mark paired: clear pairing code, set token, status connected.

    Returns the (new or existing) plaintext connector token.
    """
    row = conn.execute(
        "SELECT * FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    if not row:
        raise ValueError("Workplace not found")
    now = _now()
    token_plain = ""
    if rotate_token or not (row["connector_token"] or "").strip():
        token_plain = secrets.token_urlsafe(32)
        token_enc = encrypt_secret(token_plain)
    else:
        token_plain = decrypt_secret(str(row["connector_token"] or ""))
        token_enc = row["connector_token"]
    host = (hostname or "").strip() or (row["host"] or "tunnel")
    conn.execute(
        "UPDATE workplaces SET pairing_code='', pairing_expires_at=0, "
        "connector_token=?, connector_last_seen_at=?, connector_version=?, "
        "connector_hostname=?, host=?, status=?, updated_at=? WHERE id=?",
        (
            token_enc,
            now,
            (version or "").strip()[:64],
            (hostname or "").strip()[:128],
            host[:128],
            "connected",
            now,
            workplace_id,
        ),
    )
    conn.commit()
    return token_plain


def mark_connector_seen(
    conn: sqlite3.Connection,
    workplace_id: str,
    *,
    hostname: str = "",
    version: str = "",
    status: str = "connected",
) -> None:
    now = _now()
    sets = ["connector_last_seen_at=?", "updated_at=?", "status=?"]
    params: list[Any] = [now, now, status]
    if hostname:
        sets.append("connector_hostname=?")
        params.append(hostname.strip()[:128])
        sets.append("host=?")
        params.append(hostname.strip()[:128])
    if version:
        sets.append("connector_version=?")
        params.append(version.strip()[:64])
    params.append(workplace_id)
    conn.execute(
        f"UPDATE workplaces SET {', '.join(sets)} WHERE id=?", params
    )
    conn.commit()


def mark_connector_offline(conn: sqlite3.Connection, workplace_id: str) -> None:
    conn.execute(
        "UPDATE workplaces SET status=?, updated_at=? WHERE id=? AND kind='tunnel'",
        ("offline", _now(), workplace_id),
    )
    conn.commit()


def resolve_local_root(
    conn: sqlite3.Connection, agent_id: str
) -> str | None:
    """Return the local workplace ``root_path`` for ``agent_id``, or ``None``."""
    arow = conn.execute(
        "SELECT workplace_id FROM agents WHERE id=?", (agent_id,)
    ).fetchone()
    if not arow:
        return None
    wid = (arow["workplace_id"] or "").strip()
    if not wid:
        return None
    wrow = conn.execute(
        "SELECT kind, root_path FROM workplaces WHERE id=?", (wid,)
    ).fetchone()
    if not wrow:
        return None
    if wrow["kind"] != "local":
        return None
    path = (wrow["root_path"] or "").strip()
    return path or None


def resolve_agent_workplace(
    conn: sqlite3.Connection, agent_id: str
) -> dict[str, Any] | None:
    """Return public workplace for agent assignment, or ``None``."""
    arow = conn.execute(
        "SELECT workplace_id FROM agents WHERE id=?", (agent_id,)
    ).fetchone()
    if not arow:
        return None
    wid = (arow["workplace_id"] or "").strip()
    if not wid:
        return None
    return get_workplace(conn, wid)


__all__ = [
    "list_workplaces",
    "get_workplace",
    "get_workplace_secrets",
    "create_workplace",
    "update_workplace",
    "delete_workplace",
    "set_status",
    "issue_pairing_code",
    "find_by_pairing_code",
    "find_by_connector_token",
    "complete_pairing",
    "mark_connector_seen",
    "mark_connector_offline",
    "resolve_local_root",
    "resolve_agent_workplace",
    "public_workplace",
]
