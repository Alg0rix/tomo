"""Local process workplace — path on the Tomo host."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def test_connection(workplace: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(ok, message)``. OK when ``root_path`` exists and is a directory."""
    raw = (workplace.get("root_path") or "").strip()
    if not raw:
        return False, "Local workplace needs a root_path"
    path = Path(raw).expanduser()
    if not path.exists():
        return False, f"Path does not exist: {path}"
    if not path.is_dir():
        return False, f"Path is not a directory: {path}"
    return True, f"Local path OK: {path.resolve()}"


__all__ = ["test_connection"]
