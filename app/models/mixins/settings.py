"""Platform settings — key/value rows in the ``settings`` table.

Values are JSON-encoded. The default settings shape comes from
:func:`app.services.platform_data.seed_settings` (used to seed an empty DB and
as a fallback when the table has no rows).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.services.platform_data import seed_settings


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    if not rows:
        return dict(seed_settings())
    return {r["key"]: json.loads(r["value_json"]) for r in rows}


def update_settings(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    for key, value in data.items():
        if value is None:
            continue
        conn.execute(
            "INSERT INTO settings (key, value_json) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value)),
        )
    conn.commit()
    return get_settings(conn)

