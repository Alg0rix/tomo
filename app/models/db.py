"""SQLite connection helper.

Opens a connection to the foundation database (``DB_PATH`` by default),
ensuring the parent directory exists, enabling foreign-key enforcement, and
returning rows as :class:`sqlite3.Row` so columns are accessible by name.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.config import DB_PATH


def get_connection(path: Path | str | None = None) -> sqlite3.Connection:
    """Open an app-configured SQLite connection.

    ``path`` defaults to :data:`app.core.config.DB_PATH`. Parent directories
    are created on demand. Foreign keys are enabled and rows use
    :class:`sqlite3.Row`.
    """
    db_path = Path(path) if path is not None else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
