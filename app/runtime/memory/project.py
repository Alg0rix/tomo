"""Project memory — thin ``PROJECT.md`` under a workplace home dir.

Lane: **project** (architecture, stack, open tasks). Not USER prefs.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.core import home
from app.runtime.memory import curated

_logger = logging.getLogger(__name__)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
PROJECT_CHAR_LIMIT = 4000
_SEED = (
    "# Project notes\n\n"
    "Durable facts about this workplace: architecture, stack, conventions, "
    "open tasks. Keep entries short (§-delimited).\n"
)


def _safe_workplace_id(workplace_id: str | None) -> str | None:
    wid = (workplace_id or "").strip()
    if not wid or not _SAFE_ID.match(wid):
        return None
    if ".." in wid or "/" in wid or "\\" in wid:
        return None
    return wid


def project_dir(workplace_id: str | None, *, home_root: Path | None = None) -> Path | None:
    wid = _safe_workplace_id(workplace_id)
    if not wid:
        return None
    return home.workplaces_dir(home_root) / wid


def project_path(workplace_id: str | None, *, home_root: Path | None = None) -> Path | None:
    d = project_dir(workplace_id, home_root=home_root)
    return (d / "PROJECT.md") if d is not None else None


def ensure_project_file(
    workplace_id: str | None, *, home_root: Path | None = None
) -> Path | None:
    path = project_path(workplace_id, home_root=home_root)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(_SEED, encoding="utf-8")
    except OSError as exc:
        _logger.debug("project file ensure failed: %s", exc)
        return None
    return path


def read_entries(
    workplace_id: str | None, *, home_root: Path | None = None
) -> list[str]:
    path = ensure_project_file(workplace_id, home_root=home_root)
    if path is None:
        return []
    return curated.read_entries(path, home_root=home_root)


def format_snippet(
    workplace_id: str | None, *, limit: int = 800, home_root: Path | None = None
) -> str:
    entries = read_entries(workplace_id, home_root=home_root)
    if not entries:
        return "(empty)"
    text = curated.ENTRY_DELIMITER.join(e.strip() for e in entries if e.strip())
    if len(text) > limit:
        text = text[: limit - 12] + "\n…(truncated)"
    return text


def add_entry(
    workplace_id: str | None,
    content: str,
    *,
    home_root: Path | None = None,
) -> dict[str, Any]:
    path = ensure_project_file(workplace_id, home_root=home_root)
    if path is None:
        return {"ok": False, "error": "workplace_id required for project memory"}
    text = (content or "").strip()
    if not text:
        return {"ok": False, "error": "content is empty"}
    entries = curated.read_entries(path, home_root=home_root)
    if text in entries:
        return {"ok": True, "message": "already present", "count": len(entries)}
    dup = curated.near_duplicate(entries, text)
    if dup is not None:
        return {
            "ok": True,
            "message": "near-duplicate already present",
            "count": len(entries),
            "existing": dup[:120],
        }
    trial = entries + [text]
    joined = curated.serialize_entries(trial)
    if len(joined) > PROJECT_CHAR_LIMIT:
        return {
            "ok": False,
            "error": f"would exceed project char limit ({PROJECT_CHAR_LIMIT})",
            "count": len(entries),
        }
    curated.write_entries(path, trial, home_root=home_root)
    return {
        "ok": True,
        "message": f"added to {path.name}",
        "count": len(trial),
        "chars": len(joined),
        "path": str(path),
    }


def workplace_id_for_agent(agent_id: str | None) -> str | None:
    """Resolve bound workplace id for an agent (best-effort)."""
    aid = (agent_id or "").strip()
    if not aid:
        return None
    try:
        from app.services import store

        agent = store.get_agent(aid)
        if not agent:
            return None
        wid = (agent.get("workplace_id") or "").strip()
        return wid or None
    except Exception:
        return None


__all__ = [
    "PROJECT_CHAR_LIMIT",
    "project_dir",
    "project_path",
    "ensure_project_file",
    "read_entries",
    "format_snippet",
    "add_entry",
    "workplace_id_for_agent",
]
