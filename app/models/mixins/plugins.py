"""Plugins metadata — SQLite enable/disable (Alpha Slice G)."""

from __future__ import annotations

import sqlite3
from typing import Any


def _row_to_plugin(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "has_ui": bool(row["has_ui"]),
        "ui_path": row["ui_path"] or "",
        "created_at": row["created_at"],
    }


def list_plugins(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM plugins ORDER BY name ASC").fetchall()
    return [_row_to_plugin(r) for r in rows]


def get_plugin(conn: sqlite3.Connection, plugin_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
    return _row_to_plugin(row) if row else None


def update_plugin(
    conn: sqlite3.Connection, plugin_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
    if not row:
        return None
    enabled = row["enabled"]
    if "enabled" in data and data["enabled"] is not None:
        enabled = 1 if data["enabled"] else 0
    name = data.get("name", row["name"])
    description = data.get("description", row["description"])
    version = data.get("version", row["version"])
    conn.execute(
        "UPDATE plugins SET name=?, description=?, version=?, enabled=? WHERE id=?",
        (name, description, version, enabled, plugin_id),
    )
    conn.commit()
    return get_plugin(conn, plugin_id)


__all__ = ["list_plugins", "get_plugin", "update_plugin"]
