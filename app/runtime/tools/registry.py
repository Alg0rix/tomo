"""Tool discovery, JSON schema loading, and dispatch.

Loads every ``app/tools/*.json`` definition, exposes the OpenAI-compatible
function-tool schemas (for ``LLMClient.complete(..., tools=...)``), and
dispatches ``execute(name, arguments)`` to the matching Python backend.

For the foundation thin vertical only the ``calculator`` tool is wired; its
backend is :func:`app.runtime.tools.calculator.run`. Adding a future tool is
a matter of dropping an ``app/tools/<name>.json`` file and registering its
backend in :data:`_BACKENDS` below — dynamic ``backend``-path import is a
later task. ``execute`` always returns a string: unknown tools and missing
backends produce ``"Error: ..."`` strings rather than raising.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from app.core import config
from app.runtime.tools import calculator as _calculator_backend

ToolRunner = Callable[[dict[str, Any]], str]

# Foundation backends keyed by the tool name in each JSON ``schema.function.name``.
# Backends are repo-controlled Python modules; the JSON ``backend`` field is
# kept accurate for documentation and a future dynamic-import task.
_BACKENDS: dict[str, ToolRunner] = {
    "calculator": _calculator_backend.run,
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

    def get_openai_tools(self) -> list[dict[str, Any]]:
        """Return OpenAI function-tool schemas, ready for ``complete(tools=...)``."""
        tools: list[dict[str, Any]] = []
        for name in sorted(self._definitions):
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


def get_openai_tools() -> list[dict[str, Any]]:
    """OpenAI function-tool schemas from the default registry."""
    return get_registry().get_openai_tools()


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

