"""todo tool — simple JSON todo list under ``$TOMO_HOME/library/memory``."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from app.core import home
from app.runtime.tools.sandbox import current_agent_id

_FILE_NAME = "todos.json"


def _todos_path() -> Path:
    mem = home.library_memory_dir()
    mem.mkdir(parents=True, exist_ok=True)
    agent = current_agent_id()
    if agent:
        return mem / f"todos_{agent}.json"
    return mem / _FILE_NAME


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def _save(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")


def run(arguments: dict[str, Any]) -> str:
    """list / add / complete todos; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: todo expects a dict of arguments"
    action = arguments.get("action")
    if not isinstance(action, str) or not action.strip():
        return "Error: 'action' must be one of: list, add, complete"
    action = action.strip().lower()
    path = _todos_path()

    try:
        items = _load(path)
    except OSError as exc:
        return f"Error: could not read todos: {exc}"

    if action == "list":
        if not items:
            return "No todos"
        lines = []
        for row in items:
            mark = "x" if row.get("done") else " "
            lines.append(f"[{mark}] {row.get('id')}: {row.get('content', '')}")
        return "\n".join(lines)

    if action == "add":
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return "Error: 'content' is required for add"
        item = {
            "id": f"todo_{uuid.uuid4().hex[:8]}",
            "content": content.strip(),
            "done": False,
            "created_at": time.time(),
        }
        items.append(item)
        try:
            _save(path, items)
        except OSError as exc:
            return f"Error: could not save todos: {exc}"
        return f"Added {item['id']}: {item['content']}"

    if action == "complete":
        todo_id = arguments.get("id")
        if not isinstance(todo_id, str) or not todo_id.strip():
            return "Error: 'id' is required for complete"
        todo_id = todo_id.strip()
        found = None
        for row in items:
            if row.get("id") == todo_id:
                row["done"] = True
                found = row
                break
        if found is None:
            return f"Error: unknown todo id {todo_id!r}"
        try:
            _save(path, items)
        except OSError as exc:
            return f"Error: could not save todos: {exc}"
        return f"Completed {todo_id}: {found.get('content', '')}"

    return "Error: 'action' must be one of: list, add, complete"


__all__ = ["run"]
