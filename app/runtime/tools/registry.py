"""Tool discovery, JSON schema loading, and dispatch.

Loads every ``app/tools/*.json`` definition, exposes the OpenAI-compatible
function-tool schemas (for ``LLMClient.complete(..., tools=...)``), and
dispatches ``execute(name, arguments)`` to the matching Python backend.

Wired backends cover coding, web, process, memory, and skills tools.
Adding a tool is a matter of dropping an ``app/tools/<name>.json`` file and
registering its backend in :data:`_BACKENDS` below — dynamic ``backend``-path
import is a later task. ``execute`` always returns a string: unknown tools
and missing backends produce ``"Error: ..."`` strings rather than raising.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable

from app.core import config
from app.runtime.tools import agent_state as _agent_state_backend
from app.runtime.tools import bash as _bash_backend
from app.runtime.tools import clarify as _clarify_backend
from app.runtime.tools import create_agent as _create_agent_backend
from app.runtime.tools import delete_file as _delete_file_backend
from app.runtime.tools import delegate as _delegate_backend
from app.runtime.tools import forget_memory as _forget_memory_backend
from app.runtime.tools import list_dir as _list_dir_backend
from app.runtime.tools import list_workplaces as _list_workplaces_backend
from app.runtime.tools import patch as _patch_backend
from app.runtime.tools import portal as _portal_backend
from app.runtime.tools import process as _process_backend
from app.runtime.tools import read_file as _read_file_backend
from app.runtime.tools import recall as _recall_backend
from app.runtime.tools import register_workplace as _register_workplace_backend
from app.runtime.tools import remember as _remember_backend
from app.runtime.tools import runpy as _runpy_backend
from app.runtime.tools import save_artifact as _save_artifact_backend
from app.runtime.tools import search_files as _search_files_backend
from app.runtime.tools import session_search as _session_search_backend
from app.runtime.tools import skills_tools as _skills_tools
from app.runtime.tools import str_replace as _str_replace_backend
from app.runtime.tools import todo as _todo_backend
from app.runtime.tools import web_fetch as _web_fetch_backend
from app.runtime.tools import web_search as _web_search_backend
from app.runtime.tools import write_file as _write_file_backend

ToolRunner = Callable[[dict[str, Any]], str]

# Backends keyed by the tool name in each JSON ``schema.function.name``.
# Repo-controlled Python modules; the JSON ``backend`` field is kept accurate
# for documentation and a future dynamic-import task.
_BACKENDS: dict[str, ToolRunner] = {
    "delegate": _delegate_backend.run,
    "create_agent": _create_agent_backend.run,
    "bash": _bash_backend.run,
    "runpy": _runpy_backend.run,
    "register_workplace": _register_workplace_backend.run,
    "read_file": _read_file_backend.run,
    "write_file": _write_file_backend.run,
    "str_replace": _str_replace_backend.run,
    "patch": _patch_backend.run,
    "list_dir": _list_dir_backend.run,
    "list_workplaces": _list_workplaces_backend.run,
    "search_files": _search_files_backend.run,
    "delete_file": _delete_file_backend.run,
    "web_fetch": _web_fetch_backend.run,
    "web_search": _web_search_backend.run,
    "process": _process_backend.run,
    "todo": _todo_backend.run,
    "session_search": _session_search_backend.run,
    "list_skills": _skills_tools.list_skills_run,
    "use_skill": _skills_tools.use_skill_run,
    "manage_skill": _skills_tools.manage_skill_run,
    "clarify": _clarify_backend.run,
    "forget_memory": _forget_memory_backend.run,
    "recall": _recall_backend.run,
    "remember": _remember_backend.run,
    "agent_state": _agent_state_backend.run,
    "save_artifact": _save_artifact_backend.run,
    "portal": _portal_backend.run,
}


def _default_tools_dir() -> Path:
    return config.APP_DIR / "tools"


class ToolRegistry:
    """In-memory registry of declarative tool definitions."""

    def __init__(self, tools_dir: Path | None = None) -> None:
        self._dir = tools_dir or _default_tools_dir()
        self._definitions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Read every ``*.json`` file in the tools directory."""
        if not self._dir.is_dir():
            return
        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            name = self._tool_name(data)
            if name:
                self._definitions[name] = data

    @staticmethod
    def _tool_name(data: dict[str, Any]) -> str | None:
        """Resolve a tool's name from its OpenAI function schema or ``id``."""
        schema = data.get("schema") or {}
        fn = schema.get("function") or {}
        name = fn.get("name") or data.get("id")
        return name if isinstance(name, str) and name else None

    # --- public API -----------------------------------------------------

    def names(self) -> list[str]:
        """Sorted list of registered tool names."""
        return sorted(self._definitions)

    def get_definition(self, name: str) -> dict[str, Any] | None:
        """Return the raw JSON definition for ``name``, or ``None``."""
        data = self._definitions.get(name)
        return dict(data) if isinstance(data, dict) else None

    def list_catalog(self) -> list[dict[str, Any]]:
        """UI/API catalog rows sourced from registry JSON (not platform seed)."""
        rows: list[dict[str, Any]] = []
        for name in sorted(self._definitions):
            data = self._definitions[name]
            schema = data.get("schema") or {}
            fn = schema.get("function") or {}
            rows.append(
                {
                    "id": name,
                    "name": data.get("name") or name,
                    "description": data.get("description")
                    or fn.get("description")
                    or "",
                    "backend": data.get("backend") or "builtin",
                    "enabled": True,
                }
            )
        return rows

    def get_openai_tools(
        self, enabled: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        """Return OpenAI function-tool schemas, ready for ``complete(tools=...)``.

        When ``enabled`` is provided, only those tool names are included.
        """
        allow = set(enabled) if enabled is not None else None
        tools: list[dict[str, Any]] = []
        for name in sorted(self._definitions):
            if allow is not None and name not in allow:
                continue
            schema = self._definitions[name].get("schema")
            if isinstance(schema, dict) and schema.get("type") == "function":
                tools.append(schema)
        return tools

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Run a named tool with parsed arguments; always returns a string."""
        if name not in self._definitions:
            return f"Error: unknown tool '{name}'"
        runner = _BACKENDS.get(name)
        if runner is None:
            return f"Error: tool '{name}' has no registered backend"
        if not isinstance(arguments, dict):
            return f"Error: tool '{name}' expects a dict of arguments"
        try:
            return runner(arguments)
        except Exception as exc:  # pragma: no cover - defensive
            return f"Error: tool '{name}' failed: {exc}"


# --- module-level convenience API (used by the agent loop) ---------------

_default_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """Return the process-wide default registry, creating it lazily."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry


def get_openai_tools(enabled: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """OpenAI function-tool schemas from the default registry."""
    return get_registry().get_openai_tools(enabled)


def execute(name: str, arguments: dict[str, Any]) -> str:
    """Dispatch a tool call via the default registry; always returns a string."""
    return get_registry().execute(name, arguments)


def reset_registry() -> None:
    """Drop the cached default registry (test helper)."""
    global _default_registry
    _default_registry = None


__all__ = [
    "ToolRegistry",
    "get_registry",
    "get_openai_tools",
    "execute",
    "reset_registry",
]
