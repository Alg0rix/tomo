"""One-time move of module enablement into the live plugin catalog."""

import json
from pathlib import Path
import sqlite3


OFFICIAL_PLUGINS = {"token_monitor": "Token Monitor", "kanban": "Task Board"}


def migrate_module_catalog(conn: sqlite3.Connection, home: Path) -> None:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "modules" not in tables:
        return
    state_path = home / "plugins" / "registry.json"
    rows = json.loads(state_path.read_text()) if state_path.exists() else {}
    for row in conn.execute("SELECT id, enabled FROM modules"):
        if row["id"] not in OFFICIAL_PLUGINS:
            continue
        rows.setdefault(
            row["id"],
            {
                "id": row["id"],
                "name": OFFICIAL_PLUGINS[row["id"]],
                "description": "Official Tomo plugin",
                "version": "",
                "sdk_version": 1,
                "icon": "chart-column" if row["id"] == "token_monitor" else "columns-3",
                "path": "",
                "source": "official-pending",
                "enabled": bool(row["enabled"]),
            },
        )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rows, indent=2))
    temporary.replace(state_path)
    conn.execute("DROP TABLE modules")
    conn.commit()
