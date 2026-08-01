"""todo tool — session task list (primary plan surface).

Design:
- Always available — never behind an ATG toggle.
- Write with ``todos`` array; omit ``todos`` to read.
- ``merge=false`` (default) replaces the list; ``merge=true`` patches by id.
- Every call returns JSON ``{todos, summary}`` so the UI can render a checklist.
- Behavioral guidance lives in the tool schema description only (prompt-gated;
  the model decides when to plan — no keyword gate on user text).
- Optional ATG (``enable_atg=True``) seeds/updates the same store when used.
"""
from __future__ import annotations

import json
import logging
import threading
from contextvars import ContextVar, Token
from typing import Any

_logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})

_current: ContextVar["TodoStore | None"] = ContextVar("tomo_todo_store", default=None)
_stores_lock = threading.Lock()
_stores: dict[str, "TodoStore"] = {}


class TodoStore:
    """In-memory todo list. One instance per chat session."""

    def __init__(self) -> None:
        self._items: list[dict[str, str]] = []

    def write(
        self, todos: list[dict[str, Any]], merge: bool = False
    ) -> list[dict[str, str]]:
        if not merge:
            self._items = [self._validate(t) for t in self._dedupe_by_id(todos)]
        else:
            existing = {item["id"]: item for item in self._items}
            for t in self._dedupe_by_id(todos):
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue
                if item_id in existing:
                    if "content" in t and t["content"]:
                        existing[item_id]["content"] = str(t["content"]).strip()
                    if "status" in t and t["status"]:
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                else:
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)
            seen: set[str] = set()
            rebuilt: list[dict[str, str]] = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = rebuilt
        return self.read()

    def read(self) -> list[dict[str, str]]:
        return [item.copy() for item in self._items]

    def has_items(self) -> bool:
        return bool(self._items)

    def snapshot(self) -> dict[str, Any]:
        items = self.read()
        return {
            "todos": items,
            "summary": {
                "total": len(items),
                "pending": sum(1 for i in items if i["status"] == "pending"),
                "in_progress": sum(1 for i in items if i["status"] == "in_progress"),
                "completed": sum(1 for i in items if i["status"] == "completed"),
                "cancelled": sum(1 for i in items if i["status"] == "cancelled"),
            },
        }

    def format_active(self) -> str | None:
        """Compact active-only list for context compression reinjection."""
        active = [
            i for i in self._items if i["status"] in {"pending", "in_progress"}
        ]
        if not active:
            return None
        markers = {
            "completed": "[x]",
            "in_progress": "[>]",
            "pending": "[ ]",
            "cancelled": "[~]",
        }
        lines = ["[Active task list]"]
        for item in active:
            marker = markers.get(item["status"], "[?]")
            lines.append(f"- {marker} {item['id']}: {item['content']}")
        return "\n".join(lines)

    @staticmethod
    def _validate(item: dict[str, Any]) -> dict[str, str]:
        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip() or "(no description)"
        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            # Legacy Tomo shape: done bool
            if item.get("done") is True:
                status = "completed"
            elif item.get("done") is False:
                status = "pending"
            else:
                status = "pending"
        return {"id": item_id, "content": content, "status": status}

    @staticmethod
    def _dedupe_by_id(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last_index: dict[str, int] = {}
        for i, item in enumerate(todos):
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]


def get_store(session_id: str | None = None) -> TodoStore:
    """Return the bound store, or the session store, or a process fallback."""
    bound = _current.get()
    if bound is not None:
        return bound
    key = (session_id or "").strip() or "_default"
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = TodoStore()
            _stores[key] = store
        return store


def bind_session(session_id: str | None) -> Token:
    """Bind the session's TodoStore into the current context (for run_turn)."""
    key = (session_id or "").strip() or "_default"
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = TodoStore()
            _stores[key] = store
    return _current.set(store)


def reset_session(token: Token | None) -> None:
    if token is None:
        return
    try:
        _current.reset(token)
    except ValueError:
        _current.set(None)


def todos_from_dag(dag) -> list[dict[str, str]]:
    """Map ATG nodes to todo items in wave/topological order."""
    items: list[dict[str, str]] = []
    # Prefer structural order from waves on a fresh DAG (all pending).
    try:
        waves = dag.waves()
        ordered: list[str] = [nid for wave in waves for nid in wave]
        if not ordered:
            ordered = sorted(dag.nodes)
    except Exception:
        ordered = sorted(getattr(dag, "nodes", {}) or {})
    for nid in ordered:
        node = dag.nodes.get(nid) if hasattr(dag, "nodes") else None
        if node is None:
            continue
        status = "pending"
        st = getattr(node, "status", "pending")
        if st == "done":
            status = "completed"
        elif st == "running":
            status = "in_progress"
        elif st in {"failed", "skipped"}:
            status = "cancelled"
        goal = getattr(node, "goal", None) or nid
        items.append({"id": str(nid), "content": str(goal), "status": status})
    return items


def seed_from_dag(dag, *, session_id: str | None = None) -> dict[str, Any]:
    """Replace the session todo list with ATG nodes (visible plan)."""
    store = get_store(session_id)
    store.write(todos_from_dag(dag), merge=False)
    snap = store.snapshot()
    _logger.info(
        "ATG→todo seeded: session=%s items=%d",
        session_id or "_",
        snap["summary"]["total"],
    )
    return snap


def mark_node(
    node_id: str,
    status: str,
    *,
    session_id: str | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    """Merge a single ATG node status into the todo list."""
    store = get_store(session_id)
    patch: dict[str, Any] = {"id": node_id, "status": status}
    if content:
        patch["content"] = content
    elif not any(i["id"] == node_id for i in store.read()):
        patch["content"] = node_id
    store.write([patch], merge=True)
    return store.snapshot()


def _legacy_action(arguments: dict[str, Any], store: TodoStore) -> str | None:
    """Backward-compatible list/add/complete actions → current store."""
    action = arguments.get("action")
    if not isinstance(action, str) or not action.strip():
        return None
    action = action.strip().lower()
    if action == "list":
        return json.dumps(store.snapshot(), ensure_ascii=False)
    if action == "add":
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return "Error: 'content' is required for add"
        import uuid

        tid = f"todo_{uuid.uuid4().hex[:8]}"
        store.write(
            [{"id": tid, "content": content.strip(), "status": "pending"}],
            merge=True,
        )
        return json.dumps(store.snapshot(), ensure_ascii=False)
    if action == "complete":
        todo_id = arguments.get("id")
        if not isinstance(todo_id, str) or not todo_id.strip():
            return "Error: 'id' is required for complete"
        todo_id = todo_id.strip()
        if not any(i["id"] == todo_id for i in store.read()):
            return f"Error: unknown todo id {todo_id!r}"
        store.write(
            [{"id": todo_id, "status": "completed"}],
            merge=True,
        )
        return json.dumps(store.snapshot(), ensure_ascii=False)
    return f"Error: unknown action {action!r}"


def run(arguments: dict[str, Any]) -> str:
    """Todo tool entrypoint. Always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: todo expects a dict of arguments"

    store = get_store()

    # Legacy Tomo actions still work (tests / old prompts).
    if "action" in arguments and "todos" not in arguments:
        legacy = _legacy_action(arguments, store)
        if legacy is not None:
            return legacy

    todos = arguments.get("todos")
    merge = bool(arguments.get("merge", False))

    if todos is None:
        return json.dumps(store.snapshot(), ensure_ascii=False)

    if not isinstance(todos, list):
        return "Error: 'todos' must be an array of {id, content, status}"

    normalized: list[dict[str, Any]] = []
    for t in todos:
        if isinstance(t, dict):
            normalized.append(t)
    store.write(normalized, merge=merge)
    return json.dumps(store.snapshot(), ensure_ascii=False)


def parse_todos_payload(result: str | None) -> list[dict[str, str]] | None:
    """Extract ``todos`` array from a todo tool JSON result, if present."""
    if not result or not isinstance(result, str):
        return None
    text = result.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("todos"), list):
        return [t for t in data["todos"] if isinstance(t, dict)]
    return None


__all__ = [
    "VALID_STATUSES",
    "TodoStore",
    "get_store",
    "bind_session",
    "reset_session",
    "todos_from_dag",
    "seed_from_dag",
    "mark_node",
    "parse_todos_payload",
    "run",
]
