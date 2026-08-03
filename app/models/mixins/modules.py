"""Modules catalog — SQLite enable/disable for discovered Tomo modules."""

from __future__ import annotations

import sqlite3
from typing import Any


def _row_to_module(row: sqlite3.Row) -> dict[str, Any]:
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


def list_modules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM modules ORDER BY name ASC").fetchall()
    return [_row_to_module(r) for r in rows]


def get_module(conn: sqlite3.Connection, module_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM modules WHERE id=?", (module_id,)).fetchone()
    return _row_to_module(row) if row else None


def is_module_enabled(conn: sqlite3.Connection, module_id: str) -> bool:
    row = conn.execute(
        "SELECT enabled FROM modules WHERE id=?", (module_id,)
    ).fetchone()
    return bool(row and row["enabled"])


def update_module(
    conn: sqlite3.Connection, module_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM modules WHERE id=?", (module_id,)).fetchone()
    if not row:
        return None
    enabled = row["enabled"]
    if "enabled" in data and data["enabled"] is not None:
        enabled = 1 if data["enabled"] else 0
    name = data.get("name", row["name"])
    description = data.get("description", row["description"])
    version = data.get("version", row["version"])
    conn.execute(
        "UPDATE modules SET name=?, description=?, version=?, enabled=? WHERE id=?",
        (name, description, version, enabled, module_id),
    )
    conn.commit()
    return get_module(conn, module_id)


__all__ = ["list_modules", "get_module", "is_module_enabled", "update_module"]
