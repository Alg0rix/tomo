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
from app.models.ids import unique_id
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
    """Count agents that can use this workplace (primary, list, or scope)."""
    import json

    wrow = conn.execute(
        "SELECT kind FROM workplaces WHERE id=?", (workplace_id,)
    ).fetchone()
    kind = (wrow["kind"] if wrow else "") or ""
    rows = conn.execute(
        "SELECT workplace_id, workplace_scope, workplace_ids_json FROM agents"
    ).fetchall()
    n = 0
    for row in rows:
        keys = set(row.keys())
        scope = (
            row["workplace_scope"]
            if "workplace_scope" in keys and row["workplace_scope"]
            else "single"
        )
        if scope == "all":
            n += 1
            continue
        if scope == "all_tunnels" and kind == "tunnel":
            n += 1
            continue
        primary = (row["workplace_id"] or "").strip() if "workplace_id" in keys else ""
        if primary == workplace_id:
            n += 1
            continue
        raw = row["workplace_ids_json"] if "workplace_ids_json" in keys else "[]"
        try:
            ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except json.JSONDecodeError:
            ids = []
        if workplace_id in [str(x) for x in ids]:
            n += 1
    return n


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
        "connector_platform": col("connector_platform", "") or "",
        "connector_remote_ip": col("connector_remote_ip", "") or "",
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

    # Live hub metadata for tunnel workplaces (not persisted).
    kind = (out.get("kind") or "").strip().lower()
    if kind == "tunnel":
        try:
            from app.workplaces.hub import hub

            wid = str(out.get("id") or "")
            session = hub.get(wid) if wid else None
            out["online"] = session is not None
            if session is not None:
                if session.hostname:
                    out["connector_hostname"] = session.hostname
                if session.version:
                    out["connector_version"] = session.version
                if getattr(session, "platform", ""):
                    out["connector_platform"] = session.platform
                if getattr(session, "remote_ip", ""):
                    out["connector_remote_ip"] = session.remote_ip
                out["connector_connected_at"] = float(
                    getattr(session, "connected_at", 0) or 0
                )
                out["connector_last_seen_at"] = float(
                    getattr(session, "last_seen", 0) or out.get("connector_last_seen_at") or 0
                )
            else:
                out["online"] = False
                out["connector_connected_at"] = 0.0
        except Exception:
            out["online"] = False
            out["connector_connected_at"] = 0.0
    else:
        out["online"] = (out.get("status") or "") == "connected"
        out["connector_connected_at"] = 0.0

    # Rich host line for tiles / page subtitle.
    out["host_detail"] = _host_detail(out)
    return out


def _host_detail(wp: dict[str, Any]) -> str:
    """Human host summary including IP and hostname when known."""
    kind = (wp.get("kind") or "").strip().lower()
    if kind == "local":
        return (wp.get("root_path") or wp.get("host") or "local").strip() or "local"
    if kind == "ssh":
        user = (wp.get("ssh_user") or "").strip()
        host = (wp.get("ssh_host") or "").strip()
        port = int(wp.get("ssh_port") or 22)
        base = f"{user}@{host}" if user and host else (host or user or "ssh")
        if port and port != 22 and host:
            base = f"{base}:{port}"
        return base
    # tunnel
    parts: list[str] = []
    host = (wp.get("connector_hostname") or wp.get("host") or "").strip()
    # Strip accidental "(ip)" from stored host display for clean detail line.
    if " (" in host and host.endswith(")"):
        host = host.split(" (", 1)[0].strip()
    ip = (wp.get("connector_remote_ip") or "").strip()
    if host:
        parts.append(host)
    if ip and ip not in (host, "127.0.0.1", "::1"):
        parts.append(ip)
    elif ip and not host and ip not in ("127.0.0.1", "::1"):
        parts.append(ip)
    plat = (wp.get("connector_platform") or "").strip()
    ver = (wp.get("connector_version") or "").strip()
    # version may be "0.2.0/linux" legacy — split for display
    if "/" in ver and not plat:
        ver, plat = ver.split("/", 1)
    if plat:
        parts.append(plat)
    if ver:
        parts.append(f"v{ver}" if not ver.startswith("v") else ver)
    return " · ".join(parts) if parts else "tunnel"


def _display_host(data: dict[str, Any], kind: str) -> str:
    """Derive the tile/detail ``host`` string from kind-specific fields."""
    if kind == "local":
        return (data.get("root_path") or data.get("host") or "").strip() or "local"
    if kind == "ssh":
        user = (data.get("ssh_user") or "").strip()
        host = (data.get("ssh_host") or "").strip()
        if user and host:
            return f"{user}@{host}"
        return host or user or "ssh"
    if kind == "tunnel":
        hostname = (data.get("connector_hostname") or "").strip()
        ip = (data.get("connector_remote_ip") or "").strip()
        if hostname and ip and ip != hostname:
            return f"{hostname} ({ip})"
        if hostname:
            return hostname
        if ip:
            return ip
        return (data.get("host") or "").strip() or "tunnel"
    explicit = (data.get("host") or "").strip()
    return explicit


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
    name = (data.get("name") or "").strip() or "workplace"
    kind = (data.get("kind") or "local").strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"Invalid workplace kind: {kind}")
    prefix = {"local": "wp", "ssh": "ssh", "tunnel": "tun"}.get(kind, "wp")
    wid = unique_id(
        conn,
        "workplaces",
        name=name,
        prefix=prefix,
        explicit=(data.get("id") or None),
    )
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
            name,
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
    platform: str = "",
    remote_ip: str = "",
    rotate_token: bool = True,
) -> str:
    """Mark paired: clear pairing code, set token.

    Returns the (new or existing) plaintext connector token.
    Status is set to ``offline`` until the WebSocket registers (honest online).
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
    hn = (hostname or "").strip()[:128]
    plat = (platform or "").strip()[:64]
    ver = (version or "").strip()[:64]
    # Strip legacy "ver/platform" if client sent platform separately.
    if "/" in ver and not plat:
        ver, plat = ver.split("/", 1)
        ver, plat = ver[:64], plat[:64]
    ip = (remote_ip or "").strip()[:64]
    host = hn or ip or (row["host"] or "tunnel")
    if hn and ip and ip not in (hn, "127.0.0.1"):
        host = f"{hn} ({ip})"
    conn.execute(
        "UPDATE workplaces SET pairing_code='', pairing_expires_at=0, "
        "connector_token=?, connector_last_seen_at=?, connector_version=?, "
        "connector_hostname=?, connector_platform=?, connector_remote_ip=?, "
        "host=?, status=?, updated_at=? WHERE id=?",
        (
            token_enc,
            now,
            ver,
            hn,
            plat,
            ip,
            host[:160],
            "offline",  # not connected until live WS
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
    platform: str = "",
    remote_ip: str = "",
    status: str = "connected",
) -> None:
    now = _now()
    sets = ["connector_last_seen_at=?", "updated_at=?", "status=?"]
    params: list[Any] = [now, now, status]
    hn = (hostname or "").strip()[:128]
    plat = (platform or "").strip()[:64]
    ver = (version or "").strip()[:64]
    if "/" in ver and not plat:
        ver, plat = ver.split("/", 1)
        ver, plat = ver[:64], plat[:64]
    ip = (remote_ip or "").strip()[:64]
    if hn:
        sets.append("connector_hostname=?")
        params.append(hn)
    if ver:
        sets.append("connector_version=?")
        params.append(ver)
    if plat:
        sets.append("connector_platform=?")
        params.append(plat)
    if ip:
        sets.append("connector_remote_ip=?")
        params.append(ip)
    if hn or ip:
        host = hn or ip
        if hn and ip and ip not in (hn, "127.0.0.1"):
            host = f"{hn} ({ip})"
        sets.append("host=?")
        params.append(host[:160])
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
    """Return the local workplace ``root_path`` for ``agent_id``, or ``None``.

    Honors multi-workplace scope and primary id. Per-turn overrides are
    applied at the tool layer via :mod:`app.runtime.tools.workplace_remote`.
    """
    wp = resolve_agent_workplace(conn, agent_id)
    if not wp or (wp.get("kind") or "") != "local":
        return None
    path = (wp.get("root_path") or "").strip()
    return path or None


def resolve_agent_workplace(
    conn: sqlite3.Connection, agent_id: str
) -> dict[str, Any] | None:
    """Return public workplace for agent assignment (primary / list / scope)."""
    import json

    arow = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
    if not arow:
        return None
    keys = set(arow.keys())
    scope = (
        arow["workplace_scope"]
        if "workplace_scope" in keys and arow["workplace_scope"]
        else "single"
    )
    primary = (arow["workplace_id"] or "").strip() if "workplace_id" in keys else ""
    raw = arow["workplace_ids_json"] if "workplace_ids_json" in keys else "[]"
    try:
        ids = json.loads(raw) if isinstance(raw, str) else (raw or [])
        ids = [str(x).strip() for x in ids if str(x).strip()]
    except json.JSONDecodeError:
        ids = []

    all_wps = list_workplaces(conn)
    if scope == "all":
        allowed = all_wps
    elif scope == "all_tunnels":
        allowed = [w for w in all_wps if (w.get("kind") or "") == "tunnel"]
    else:
        if primary and primary not in ids:
            ids = [primary] + ids
        by_id = {w["id"]: w for w in all_wps}
        allowed = [by_id[i] for i in ids if i in by_id]

    if not allowed:
        return None
    if primary:
        for w in allowed:
            if w["id"] == primary:
                return w
    return allowed[0]


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
