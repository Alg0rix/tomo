"""MCP server/item persistence — CRUD over ``mcp_servers`` / ``mcp_items``.

Each server config is a local ``stdio`` command or a remote ``streamable_http``
endpoint. Environment values (``stdio``) and header values (``streamable_http``)
are secret maps encrypted at rest via :mod:`app.core.secrets`, the same Fernet
scheme used for LLM profile API keys.

Secret contract:

* ``env`` / ``headers`` are **ciphertext** (JSON-encoded map, then encrypted)
  at rest — never plaintext in the DB columns.
* Public views (:func:`public_server`) never include the raw map or its
  values — only ``env_keys`` / ``headers_keys`` (key names) and
  ``env_set`` / ``headers_set`` booleans.
* Decrypted maps are only ever returned by :func:`decrypted_server`, used
  internally by the MCP connection manager to launch/authenticate — never
  serialized to HTTP/HTML.
* On update, a masked placeholder (``••••``) for a given key preserves that
  key's existing value; keys omitted from a supplied map are dropped (the
  supplied map *replaces* the stored one); omitting the map key entirely
  (not present in ``data``) leaves the stored ciphertext untouched.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from app.core.secrets import decrypt_secret, encrypt_secret

_TRANSPORTS = {"stdio", "streamable_http"}
_KINDS = {"tool", "resource", "resource_template", "prompt"}
_MASK = "••••"


def _now() -> float:
    return time.time()


def _safe_json(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _decrypt_map(ciphertext: str | None) -> dict[str, str]:
    raw = decrypt_secret(ciphertext or "")
    if not raw:
        return {}
    data = _safe_json(raw, {})
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _encrypt_map(mapping: dict[str, str]) -> str:
    if not mapping:
        return ""
    return encrypt_secret(json.dumps(mapping, sort_keys=True))


def _merge_secret_map(existing_ciphertext: str, incoming: dict[str, Any] | None) -> str:
    """Return new ciphertext for ``incoming`` merged over ``existing_ciphertext``.

    ``incoming is None`` means the field was omitted entirely — preserve as-is.
    Otherwise the supplied map replaces the stored one, except keys whose
    value is the mask placeholder keep the prior value for that key.
    """
    if incoming is None:
        return existing_ciphertext
    existing = _decrypt_map(existing_ciphertext)
    merged: dict[str, str] = {}
    for key, value in incoming.items():
        key = str(key)
        if value == _MASK:
            if key in existing:
                merged[key] = existing[key]
            continue
        merged[key] = str(value)
    return _encrypt_map(merged)


def _base_server(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "transport": row["transport"],
        "command": row["command"],
        "args": _safe_json(row["args_json"], []),
        "url": row["url"],
        "env_ciphertext": row["env_ciphertext"],
        "headers_ciphertext": row["headers_ciphertext"],
        "enabled": bool(row["enabled"]),
        "status": row["status"],
        "status_message": row["status_message"],
        "server_info": _safe_json(row["server_info_json"], {}),
        "capabilities": _safe_json(row["capabilities_json"], {}),
        "last_connected_at": row["last_connected_at"],
        "last_discovered_at": row["last_discovered_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def public_server(row: dict[str, Any]) -> dict[str, Any]:
    """Safe-for-HTTP/HTML view: key names only, never secret values."""
    env = _decrypt_map(row.get("env_ciphertext"))
    headers = _decrypt_map(row.get("headers_ciphertext"))
    out = {k: v for k, v in row.items() if k not in ("env_ciphertext", "headers_ciphertext")}
    out["env_keys"] = sorted(env.keys())
    out["headers_keys"] = sorted(headers.keys())
    out["env_set"] = bool(env)
    out["headers_set"] = bool(headers)
    return out


def decrypted_server(row: dict[str, Any]) -> dict[str, Any]:
    """Runtime view with decrypted ``env``/``headers`` maps (manager use only)."""
    out = {k: v for k, v in row.items() if k not in ("env_ciphertext", "headers_ciphertext")}
    out["env"] = _decrypt_map(row.get("env_ciphertext"))
    out["headers"] = _decrypt_map(row.get("headers_ciphertext"))
    return out


def list_servers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM mcp_servers ORDER BY created_at ASC").fetchall()
    return [public_server(_base_server(r)) for r in rows]


def get_server(
    conn: sqlite3.Connection, server_id: str, *, include_secrets: bool = False
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
    if not row:
        return None
    base = _base_server(row)
    return decrypted_server(base) if include_secrets else public_server(base)


def _validate_transport(transport: str, command: str, url: str) -> None:
    if transport not in _TRANSPORTS:
        raise ValueError(f"transport must be one of {sorted(_TRANSPORTS)}")
    if transport == "stdio" and not (command or "").strip():
        raise ValueError("command is required for stdio servers")
    if transport == "streamable_http" and not (url or "").strip():
        raise ValueError("url is required for streamable_http servers")


def _validate_args(args: Any) -> list[str]:
    if args is None:
        return []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    return args


def create_server(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    from app.models.ids import unique_id

    name = (data.get("name") or "").strip() or "mcp-server"
    transport = str(data.get("transport") or "").strip()
    command = str(data.get("command") or "").strip()
    url = str(data.get("url") or "").strip()
    _validate_transport(transport, command, url)
    args = _validate_args(data.get("args"))

    sid = unique_id(conn, "mcp_servers", name=name, prefix="", explicit=(data.get("id") or None))
    now = _now()
    conn.execute(
        "INSERT INTO mcp_servers "
        "(id, name, transport, command, args_json, url, env_ciphertext, "
        "headers_ciphertext, enabled, status, status_message, server_info_json, "
        "capabilities_json, last_connected_at, last_discovered_at, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sid,
            name,
            transport,
            command,
            json.dumps(args),
            url,
            _encrypt_map(dict(data.get("env") or {})),
            _encrypt_map(dict(data.get("headers") or {})),
            1 if data.get("enabled", True) else 0,
            "unknown",
            "",
            "{}",
            "{}",
            0,
            0,
            now,
            now,
        ),
    )
    conn.commit()
    return get_server(conn, sid)


def update_server(
    conn: sqlite3.Connection, server_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM mcp_servers WHERE id=?", (server_id,)).fetchone()
    if not row:
        return None
    base = _base_server(row)

    transport = data.get("transport") if data.get("transport") is not None else base["transport"]
    command = data.get("command") if "command" in data and data.get("command") is not None else base["command"]
    url = data.get("url") if "url" in data and data.get("url") is not None else base["url"]
    _validate_transport(str(transport), str(command), str(url))

    sets: list[str] = []
    params: list[Any] = []
    if "name" in data and data["name"] is not None:
        sets.append("name=?")
        params.append(str(data["name"]).strip() or base["name"])
    if "transport" in data and data["transport"] is not None:
        sets.append("transport=?")
        params.append(str(data["transport"]))
    if "command" in data and data["command"] is not None:
        sets.append("command=?")
        params.append(str(data["command"]))
    if "args" in data:
        sets.append("args_json=?")
        params.append(json.dumps(_validate_args(data.get("args"))))
    if "url" in data and data["url"] is not None:
        sets.append("url=?")
        params.append(str(data["url"]))
    if "env" in data:
        sets.append("env_ciphertext=?")
        params.append(_merge_secret_map(base["env_ciphertext"], data.get("env")))
    if "headers" in data:
        sets.append("headers_ciphertext=?")
        params.append(_merge_secret_map(base["headers_ciphertext"], data.get("headers")))
    if "enabled" in data and data["enabled"] is not None:
        sets.append("enabled=?")
        params.append(1 if data["enabled"] else 0)
    if sets:
        sets.append("updated_at=?")
        params.append(_now())
        params.append(server_id)
        conn.execute(f"UPDATE mcp_servers SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return get_server(conn, server_id)


def delete_server(conn: sqlite3.Connection, server_id: str) -> bool:
    if not conn.execute("SELECT 1 FROM mcp_servers WHERE id=?", (server_id,)).fetchone():
        return False
    conn.execute("DELETE FROM mcp_servers WHERE id=?", (server_id,))
    conn.commit()
    return True


def set_status(
    conn: sqlite3.Connection,
    server_id: str,
    status: str,
    message: str = "",
    *,
    connected_at: float | None = None,
    discovered_at: float | None = None,
    server_info: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not conn.execute("SELECT 1 FROM mcp_servers WHERE id=?", (server_id,)).fetchone():
        return None
    sets = ["status=?", "status_message=?", "updated_at=?"]
    params: list[Any] = [status, message[:2000], _now()]
    if connected_at is not None:
        sets.append("last_connected_at=?")
        params.append(connected_at)
    if discovered_at is not None:
        sets.append("last_discovered_at=?")
        params.append(discovered_at)
    if server_info is not None:
        sets.append("server_info_json=?")
        params.append(json.dumps(server_info))
    if capabilities is not None:
        sets.append("capabilities_json=?")
        params.append(json.dumps(capabilities))
    params.append(server_id)
    conn.execute(f"UPDATE mcp_servers SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()
    return get_server(conn, server_id)


def reset_runtime_statuses(conn: sqlite3.Connection) -> None:
    """Reset every server's ``status`` to ``unknown`` (process-restart trust reset)."""
    conn.execute("UPDATE mcp_servers SET status='unknown'")
    conn.commit()


# -- items --------------------------------------------------------------


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "server_id": row["server_id"],
        "kind": row["kind"],
        "runtime_id": row["runtime_id"],
        "name": row["name"],
        "title": row["title"],
        "description": row["description"],
        "uri": row["uri"],
        "mime_type": row["mime_type"],
        "schema": _safe_json(row["schema_json"], {}),
        "metadata": _safe_json(row["metadata_json"], {}),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_items(
    conn: sqlite3.Connection,
    server_id: str,
    *,
    kind: str | None = None,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM mcp_items WHERE server_id=?"
    params: list[Any] = [server_id]
    if kind is not None:
        sql += " AND kind=?"
        params.append(kind)
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY kind ASC, name ASC"
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_item(r) for r in rows]


def replace_items(
    conn: sqlite3.Connection, server_id: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Replace every item row for ``server_id``, preserving prior per-item enablement."""
    from app.models.ids import unique_id

    prior_enabled: dict[tuple[str, str, str], bool] = {}
    for row in conn.execute(
        "SELECT kind, name, uri, enabled FROM mcp_items WHERE server_id=?", (server_id,)
    ).fetchall():
        prior_enabled[(row["kind"], row["name"], row["uri"])] = bool(row["enabled"])

    conn.execute("DELETE FROM mcp_items WHERE server_id=?", (server_id,))
    now = _now()
    out: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("kind") or "")
        if kind not in _KINDS:
            raise ValueError(f"kind must be one of {sorted(_KINDS)}")
        name = str(item.get("name") or "")
        uri = str(item.get("uri") or "")
        key = (kind, name, uri)
        enabled = prior_enabled.get(key, True)
        item_id = unique_id(
            conn, "mcp_items", name=f"{server_id}-{kind}-{name or uri}", prefix="mcpitem"
        )
        conn.execute(
            "INSERT INTO mcp_items "
            "(id, server_id, kind, runtime_id, name, title, description, uri, "
            "mime_type, schema_json, metadata_json, enabled, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                server_id,
                kind,
                str(item.get("runtime_id") or ""),
                name,
                str(item.get("title") or ""),
                str(item.get("description") or ""),
                uri,
                str(item.get("mime_type") or ""),
                json.dumps(item.get("schema") or {}),
                json.dumps(item.get("metadata") or {}),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        out.append(_row_to_item(conn.execute("SELECT * FROM mcp_items WHERE id=?", (item_id,)).fetchone()))
    conn.commit()
    return out


def set_item_enabled(
    conn: sqlite3.Connection, item_id: str, enabled: bool
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM mcp_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE mcp_items SET enabled=?, updated_at=? WHERE id=?",
        (1 if enabled else 0, _now(), item_id),
    )
    conn.commit()
    return _row_to_item(conn.execute("SELECT * FROM mcp_items WHERE id=?", (item_id,)).fetchone())


def get_item(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM mcp_items WHERE id=?", (item_id,)).fetchone()
    return _row_to_item(row) if row else None


def get_item_by_runtime_id(conn: sqlite3.Connection, runtime_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM mcp_items WHERE runtime_id=?", (runtime_id,)).fetchone()
    return _row_to_item(row) if row else None


__all__ = [
    "public_server",
    "decrypted_server",
    "list_servers",
    "get_server",
    "create_server",
    "update_server",
    "delete_server",
    "set_status",
    "reset_runtime_statuses",
    "list_items",
    "replace_items",
    "set_item_enabled",
    "get_item",
    "get_item_by_runtime_id",
]
