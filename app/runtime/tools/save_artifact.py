"""``save_artifact`` tool — catalog files/outputs from past tasks."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import current_agent_id


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    title = arguments.get("title")
    if not isinstance(title, str) or not title.strip():
        return "Error: title is required"
    path = str(arguments.get("path") or "").strip()
    kind = str(arguments.get("kind") or "file").strip() or "file"
    notes = str(arguments.get("notes") or "").strip()
    session_id = str(arguments.get("session_id") or "").strip()
    agent_id = str(arguments.get("agent_id") or current_agent_id() or "").strip()

    from app.services import store

    try:
        art = store.create_artifact(
            {
                "title": title.strip(),
                "path": path,
                "kind": kind,
                "notes": notes,
                "session_id": session_id,
                "agent_id": agent_id,
            }
        )
    except ValueError as exc:
        return f"Error: {exc}"
    return f"Saved artifact '{art['id']}': {art['title']}" + (
        f" → {art['path']}" if art.get("path") else ""
    )


__all__ = ["run"]
