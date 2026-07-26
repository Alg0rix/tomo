"""Workplaces — CRUD + Connect over the ``workplaces`` table (Alpha Slice D).

Kinds:

* ``local`` — path on disk; Connect checks the directory exists.
* ``ssh`` — remote host; password/key stored as Fernet ciphertext
  (``enc:v1:`` via :mod:`app.core.secrets`). Connect probes via the SSH
  backend (mocked in unit tests).
* ``tunnel`` — Tomo Connector stub. Allowed in the schema but Connect never
  reports connected — status stays ``later`` with an honest label.

Public views never expose decrypted SSH secrets; they expose ``password_set`` /
``key_set`` and masked placeholders. Blank password/key on update keeps the
existing ciphertext (same contract as LLM profiles).
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from app.core.secrets import decrypt_secret, encrypt_secret

_KINDS = frozenset({"local", "ssh", "tunnel"})
_STATUSES = frozenset({"offline", "connected", "later"})


def _now() -> float:
    return time.time()


def _agent_count(conn: sqlite3.Connection, workplace_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM agents WHERE workplace_id=?",
        (workplace_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def _row_to_workplace(row: sqlite3.Row, agent_count: int) -> dict[str, Any]:
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
        "ssh_password": row["ssh_password"],  # ciphertext at rest
        "ssh_key": row["ssh_key"],  # ciphertext at rest
        "agent_count": agent_count,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def public_workplace(row: dict[str, Any]) -> dict[str, Any]:
    """Drop ciphertext; add ``password_set`` / ``key_set`` — safe for HTTP/HTML."""
    out = dict(row)
    pwd = decrypt_secret(str(out.pop("ssh_password", "") or ""))
    key = decrypt_secret(str(out.pop("ssh_key", "") or ""))
    out["password_set"] = bool(pwd)
    out["key_set"] = bool(key)
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
        return (data.get("ssh_host") or data.get("root_path") or "").strip() or "connector later"
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
    return wp


def create_workplace(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    wid = data["id"]
    if conn.execute("SELECT 1 FROM workplaces WHERE id=?", (wid,)).fetchone():
        raise ValueError("Workplace ID already exists")
    kind = (data.get("kind") or "local").strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"Invalid workplace kind: {kind}")
    # Tunnel starts as honest "later"; others offline until Connect succeeds.
    status = "later" if kind == "tunnel" else "offline"
    now = _now()
    host = _display_host(data, kind)
    conn.execute(
        "INSERT INTO workplaces (id, name, kind, status, host, root_path, "
        "ssh_host, ssh_port, ssh_user, ssh_password, ssh_key, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
        if kind == "tunnel":
            sets.append("status=?")
            params.append("later")
    for key in ("name", "root_path", "ssh_host", "ssh_user"):
        if key in data and data[key] is not None:
            sets.append(f"{key}=?")
            params.append(str(data[key]).strip() if isinstance(data[key], str) else data[key])
    if "ssh_port" in data and data["ssh_port"] is not None:
        sets.append("ssh_port=?")
        params.append(int(data["ssh_port"]))
    # Blank password/key keeps existing ciphertext.
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
        # Recompute display host from the merged view.
        merged = {
            "host": "",
            "root_path": data.get("root_path", row["root_path"]),
            "ssh_host": data.get("ssh_host", row["ssh_host"]),
            "ssh_user": data.get("ssh_user", row["ssh_user"]),
        }
        sets.append("host=?")
        params.append(_display_host(merged, kind))
    if "status" in data and data["status"] is not None:
        st = str(data["status"]).strip().lower()
        if st in _STATUSES:
            # Never allow tunnel to flip to connected via raw status write.
            if kind == "tunnel" and st == "connected":
                st = "later"
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
    # Clear agent assignments pointing at this workplace.
    conn.execute(
        "UPDATE agents SET workplace_id='' WHERE workplace_id=?",
        (workplace_id,),
    )
    conn.execute("DELETE FROM workplaces WHERE id=?", (workplace_id,))
    conn.commit()
    return True


def set_status(
    conn: sqlite3.Connection, workplace_id: str, status: str
) -> dict[str, Any] | None:
    return update_workplace(conn, workplace_id, {"status": status})


def resolve_local_root(
    conn: sqlite3.Connection, agent_id: str
) -> str | None:
    """Return the local workplace ``root_path`` for ``agent_id``, or ``None``.

    Only local workplaces with a non-empty path qualify. SSH/tunnel do not
    change the bash/file cwd in Alpha (Connect-only for SSH; connector later
    for tunnel) — callers fall back to the agent ``work/`` dir.
    """
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


__all__ = [
    "list_workplaces",
    "get_workplace",
    "get_workplace_secrets",
    "create_workplace",
    "update_workplace",
    "delete_workplace",
    "set_status",
    "resolve_local_root",
    "public_workplace",
]
