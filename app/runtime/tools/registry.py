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

import asyncio
import json
import logging
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Iterable

from app.core import config
from app.core.observability import observe
from app.runtime.tools import swarm_board as _swarm_board_backend

logger = logging.getLogger(__name__)

ToolRunner = Callable[[dict[str, Any]], str]

# Backends keyed by the tool name in each JSON ``schema.function.name``.
# Only these repo-controlled import paths may execute. The JSON backend
# field stays descriptive; catalog discovery never imports tool implementations.
_BACKENDS: dict[str, str | ToolRunner] = {
    "search_tools": "app.runtime.tools.discovery:run",
    "plugin_manager": "app.runtime.tools.plugin_manager:run",
    "delegate": "app.runtime.tools.delegate:run",
    "create_agent": "app.runtime.tools.create_agent:run",
    "bash": "app.runtime.tools.bash:run",
    "runpy": "app.runtime.tools.runpy:run",
    "register_workplace": "app.runtime.tools.register_workplace:run",
    "read_file": "app.runtime.tools.read_file:run",
    "write_file": "app.runtime.tools.write_file:run",
    "str_replace": "app.runtime.tools.str_replace:run",
    "patch": "app.runtime.tools.patch:run",
    "list_dir": "app.runtime.tools.list_dir:run",
    "list_workplaces": "app.runtime.tools.list_workplaces:run",
    "search_files": "app.runtime.tools.search_files:run",
    "delete_file": "app.runtime.tools.delete_file:run",
    "vision_analyze": "app.runtime.tools.vision_analyze:run",
    "web_fetch": "app.runtime.tools.web_fetch:run",
    "web_search": "app.runtime.tools.web_search:run",
    "browser": "app.runtime.tools.browser:run",
    "process": "app.runtime.tools.process:run",
    "todo": "app.runtime.tools.todo:run",
    "session_search": "app.runtime.tools.session_search:run",
    "schedule": "app.runtime.tools.schedule:run",
    "list_skills": "app.runtime.tools.skills_tools:list_skills_run",
    "use_skill": "app.runtime.tools.skills_tools:use_skill_run",
    "manage_skill": "app.runtime.tools.skills_tools:manage_skill_run",
    "clarify": "app.runtime.tools.clarify:run",
    "render_ui": "app.runtime.tools.render_ui:run",
    "record_episode": "app.runtime.tools.record_episode:run",
    "recall_episodes": "app.runtime.tools.recall_episodes:run",
    "agent_state": "app.runtime.tools.agent_state:run",
    "save_artifact": "app.runtime.tools.save_artifact:run",
    "telegram_send_file": "app.runtime.tools.telegram_send_file:run",
    "list_artifacts": "app.runtime.tools.list_artifacts:run",
    "fetch_artifact": "app.runtime.tools.fetch_artifact:run",
    "portal": "app.runtime.tools.portal:run",
    "memory": "app.runtime.tools.memory:run",
    "agent_info": "app.runtime.tools.agent_info:run",
    "swarm_board": "app.runtime.tools.swarm_board:run",
    "start_swarm": "app.runtime.tools.start_swarm:run",
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

    def _live_definitions(self) -> dict[str, dict[str, Any]]:
        from app.plugins.manager import get_manager
        return {**self._definitions, **get_manager().definitions()}

    def names(self) -> list[str]:
        """Sorted list of registered tool names."""
        return sorted(self._live_definitions())

    def get_definition(self, name: str) -> dict[str, Any] | None:
        """Return the raw JSON definition for ``name``, or ``None``."""
        data = self._live_definitions().get(name)
        return dict(data) if isinstance(data, dict) else None

    def list_catalog(self) -> list[dict[str, Any]]:
        """UI/API catalog rows sourced from registry JSON (not platform seed)."""
        rows: list[dict[str, Any]] = []
        for name, data in sorted(self._live_definitions().items()):
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
        from app.runtime.access import current_execution
        from app.runtime.policy import tool_available
        context = current_execution(required=False)
        tools: list[dict[str, Any]] = []
        for name, data in sorted(self._live_definitions().items()):
            if allow is not None and name not in allow:
                continue
            if context is not None and not tool_available(context, name):
                continue
            schema = data.get("schema")
            if isinstance(schema, dict) and schema.get("type") == "function":
                tools.append(schema)
        return tools

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """Run a named tool with parsed arguments; always returns a string."""
        from app.runtime.access import AccessDenied
        from app.runtime.policy import authorize_tool, normalize_tool_arguments
        try:
            context = authorize_tool(name, arguments)
            arguments = normalize_tool_arguments(context, name, arguments)
        except AccessDenied as exc:
            return f"Error: {exc}"
        if name.startswith("plugin__") and context.role != "admin":
            # Members never execute plugin code in the coordinator process.
            try:
                from app.runtime.plugins.member_sandbox import sandboxed_plugin_call

                plugin_id = _member_plugin_id(name)
                result = sandboxed_plugin_call(context, plugin_id, name, arguments)
            except AccessDenied as exc:
                result = f"Error: {exc}"
            except Exception:
                result = "Error: Member tool execution failed"
            from app.services import store as _store
            _store.access.audit(context.user_id, "tool." + name, session_id=context.session_id,
                                agent_id=context.agent_id, destination_id=context.destination_id,
                                outcome="failed" if result.startswith("Error:") else "ok")
            return result
        from app.services import store
        try:
            result = self._execute_raw(name, arguments)
        except Exception:
            result = "Error: tool service failed"
        store.access.audit(context.user_id, "tool." + name, session_id=context.session_id,
                           agent_id=context.agent_id, destination_id=context.destination_id,
                           outcome="failed" if result.startswith("Error:") else "ok")
        return result

    def _execute_raw(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "bash" and isinstance(arguments, dict):
            raw = arguments.get('background')
            background = raw.strip().lower() in {'1', 'true', 'yes', 'on'} if isinstance(raw, str) else bool(raw)
            if background:
                from app.services.background_jobs import manager
                job = manager.start(arguments.get('command'), arguments.get('workplace') or arguments.get('workplace_id'))
                return (f"Started background job {job['id']}\nstatus: {job['status']}\n"
                        f"backend: {job['backend']}\nworkplace: {job['workplace_id']}")
        if name.startswith("plugin__"):
            from app.plugins.manager import get_manager
            if not isinstance(arguments, dict):
                return "Error: tool expects a dict of arguments"
            return get_manager().execute(name, arguments)
        if name not in self._definitions:
            return f"Error: unknown tool '{name}'"
        runner = _BACKENDS.get(name)
        if runner is None:
            return f"Error: tool '{name}' has no registered backend"
        if not isinstance(arguments, dict):
            return f"Error: tool '{name}' expects a dict of arguments"
        try:
            if isinstance(runner, str):
                module, function = runner.split(":", 1)
                runner = getattr(import_module(module), function)
            return runner(arguments)
        except Exception as exc:  # pragma: no cover - defensive
            # Model-facing text stays generic: backend tracebacks may echo
            # paths, arguments, or environment. The error class goes to the
            # operator log only, never the raw message (credential-bearing
            # exceptions must not reach logs or models verbatim).
            logger.warning("Tool backend failed: tool=%s error=%s", name, type(exc).__name__)
            return f"Error: tool '{name}' failed"


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


def is_mcp_tool_name(name: str) -> bool:
    """True for a namespaced ``mcp__<server>__<tool>`` runtime id."""
    from app.runtime.mcp.names import is_mcp_runtime_id

    return is_mcp_runtime_id(name)


def _audit_tool(name: str, outcome: str) -> None:
    from app.runtime.access import current_execution
    from app.services import store
    context = current_execution(required=False)
    if context:
        import re
        action = "tool." + name if isinstance(name, str) else "tool.invalid"
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,100}", action):
            action = "tool.invalid"
        store.access.audit(context.user_id, action, session_id=context.session_id,
                           agent_id=context.agent_id, destination_id=context.destination_id,
                           outcome=outcome)


def _member_plugin_id(name: str) -> str:
    """Split ``plugin__<id>__<tool>`` without ambiguity (ids may hold ``__``)."""
    from app.plugins.manager import get_manager

    with get_manager()._lock:
        # Longest id first: ids may themselves contain "__".
        for plugin_id in sorted(get_manager()._rows, key=len, reverse=True):
            if name.startswith(f"plugin__{plugin_id}__") and len(name) > len(f"plugin__{plugin_id}__"):
                return plugin_id
    return name.split("__", 2)[1] if "__" in name else name


@observe("tool")
async def execute_async(name: str, arguments: dict[str, Any]) -> str:
    """MCP/channel I/O awaits its live transport; other built-ins use a worker thread."""
    from app.runtime.access import AccessDenied
    from app.runtime.policy import authorize_tool
    try:
        authorize_tool(name, arguments)
    except AccessDenied as exc:
        return f"Error: {exc}"
    denial = _swarm_board_backend.authorize(name, arguments)
    if denial:
        return denial
    if is_mcp_tool_name(name):
        from app.runtime.mcp import mcp_manager

        result = await mcp_manager.call_tool(name, arguments)
        _audit_tool(name, "failed" if result.startswith("Error:") else "ok")
        return result
    if name == "telegram_send_file":
        from app.runtime.tools.telegram_send_file import run_async

        result = await run_async(arguments)
        _audit_tool(name, "failed" if result.startswith("Error:") else "ok")
        return result
    return await asyncio.to_thread(execute, name, arguments)


__all__ = [
    "ToolRegistry",
    "get_registry",
    "get_openai_tools",
    "execute",
    "execute_async",
    "is_mcp_tool_name",
    "reset_registry",
]
