"""Process-local live MCP sessions: connect, discover, reconnect, call.

Persistence (server/item rows, status) lives in SQLite via ``app.services.store``;
this module owns only the in-memory ``ClientSession``/transport lifecycle,
which cannot survive a process restart. Only ``stdio`` (subprocess, argv,
``shell=False``) and Streamable HTTP transports are supported.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable

import httpx  # mcp<2 expects real httpx types; keep this client on httpx until mcp v2

from app.runtime.mcp.discovery import (
    normalize_prompt,
    normalize_resource,
    normalize_resource_template,
    normalize_tool,
    paginate_mcp_list,
)
from app.runtime.mcp.names import split_runtime_tool_id
from app.runtime.mcp.results import (
    render_prompt_result,
    render_resource_result,
    render_tool_result,
)
from app.services import store

_ERROR_LIMIT = 500

# Injected in tests to avoid real subprocesses/network listeners.
SessionFactory = Callable[[dict[str, Any]], Awaitable[tuple[AsyncExitStack, Any, Any]]]


def _bounded_error(exc: BaseException, *, limit: int = _ERROR_LIMIT) -> str:
    return f"{type(exc).__name__}: {exc}"[:limit]


async def _to_thread_sandboxed_stdio(context, server, item, arguments) -> str:
    """Run the blocking container sandbox off the event loop."""
    import asyncio as _asyncio

    from app.runtime.mcp.member_sandbox import sandboxed_stdio_call

    return await _asyncio.to_thread(sandboxed_stdio_call, context, server, item, arguments)


def _dump(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", exclude_none=True)
        except TypeError:
            return dump()
    return {}


class _Live:
    __slots__ = ("stack", "session")

    def __init__(self, stack: AsyncExitStack, session: Any) -> None:
        self.stack = stack
        self.session = session


class McpConnectionManager:
    """Owns every live ``ClientSession`` for the process's lifetime."""

    def __init__(self) -> None:
        self._live: dict[str, _Live] = {}
        self._connect_locks: dict[str, asyncio.Lock] = {}
        # Test seam: replaces real transport/session creation entirely.
        self.session_factory: SessionFactory | None = None

    def _connect_lock(self, server_id: str) -> asyncio.Lock:
        lock = self._connect_locks.get(server_id)
        if lock is None:
            lock = asyncio.Lock()
            self._connect_locks[server_id] = lock
        return lock

    def connected_server_ids(self) -> set[str]:
        return set(self._live.keys())

    # -- session lifecycle -------------------------------------------------

    async def _create_session(self, server: dict[str, Any]) -> tuple[AsyncExitStack, Any, Any]:
        if self.session_factory is not None:
            return await self.session_factory(server)
        # Merely checking namespaced tool IDs must not load the MCP SDK.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client

        stack = AsyncExitStack()
        try:
            if server["transport"] == "stdio":
                params = StdioServerParameters(
                    command=server["command"],
                    args=list(server.get("args") or []),
                    env=(dict(server.get("env") or {}) or None),
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif server["transport"] == "streamable_http":
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=dict(server.get("headers") or {}), follow_redirects=True
                    )
                )
                read, write, _get_session_id = await stack.enter_async_context(
                    streamable_http_client(server["url"], http_client=http_client)
                )
            else:
                raise ValueError(f"unsupported transport: {server['transport']}")
            session = await stack.enter_async_context(ClientSession(read, write))
            init_result = await session.initialize()
            return stack, session, init_result
        except Exception:
            await stack.aclose()
            raise

    async def _discover(self, server: dict[str, Any], session: Any) -> list[dict[str, Any]]:
        tools_raw = await paginate_mcp_list(lambda c: session.list_tools(c), "tools")
        resources_raw = await paginate_mcp_list(lambda c: session.list_resources(c), "resources")
        templates_raw = await paginate_mcp_list(
            lambda c: session.list_resource_templates(c), "resourceTemplates"
        )
        prompts_raw = await paginate_mcp_list(lambda c: session.list_prompts(c), "prompts")
        return (
            [normalize_tool(server, t) for t in tools_raw]
            + [normalize_resource(server, r) for r in resources_raw]
            + [normalize_resource_template(server, r) for r in templates_raw]
            + [normalize_prompt(server, p) for p in prompts_raw]
        )

    async def _close_live(self, server_id: str) -> None:
        live = self._live.get(server_id)
        if live is not None:
            await live.stack.aclose()
            self._live.pop(server_id, None)

    async def connect_and_discover(self, server_id: str) -> dict[str, Any]:
        """(Re)connect ``server_id``, discover its capabilities, and persist them.

        Discovery is all-or-nothing: ``mcp_items`` are only replaced once every
        capability family listed successfully, so a partial failure never
        clobbers a previously-good snapshot the UI can still show for repair.
        """
        from app.runtime.access import current_execution
        if current_execution(required=False):
            self._require_external_admin()
        async with self._connect_lock(server_id):
            server = store.get_mcp_server(server_id, include_secrets=True)
            if server is None:
                raise ValueError(f"unknown MCP server: {server_id}")
            if not server["enabled"]:
                await self._close_live(server_id)
                return store.set_mcp_status(server_id, "disabled", "server is disabled")

            live = self._live.get(server_id)
            if live is not None:
                return store.get_mcp_server(server_id)

            try:
                stack, session, init_result = await self._create_session(server)
            except Exception as exc:
                return store.set_mcp_status(server_id, "error", _bounded_error(exc))

            try:
                items = await self._discover(server, session)
            except Exception as exc:
                await stack.aclose()
                return store.set_mcp_status(server_id, "error", _bounded_error(exc))

            store.replace_mcp_items(server_id, items)
            now = time.time()
            result = store.set_mcp_status(
                server_id,
                "connected",
                "",
                connected_at=now,
                discovered_at=now,
                server_info=_dump(getattr(init_result, "serverInfo", None)),
                capabilities=_dump(getattr(init_result, "capabilities", None)),
            )
            self._live[server_id] = _Live(stack, session)
            return result

    async def ensure_for_servers(self, server_ids: set[str]) -> set[str]:
        """Connect every enabled requested server; return only the live ones.

        A cheap no-op for servers already connected — this runs before every
        agent turn, so it must not re-run discovery when nothing changed.

        Members never spawn host sessions here: their calls execute per-call
        in their own per-chat container from the cached catalog, so this
        returns the cached-enabled subset without starting any transport.
        """
        from app.runtime.access import current_execution
        context = current_execution(required=False)
        if context is not None and context.role != "admin":
            return self.cached_enabled_ids(server_ids)
        if current_execution(required=False):
            self._require_external_admin()
        connected: set[str] = set()
        for sid in server_ids:
            server = store.get_mcp_server(sid)
            if server is None or not server["enabled"]:
                continue
            if sid in self._live:
                connected.add(sid)
                continue
            result = await self.connect_and_discover(sid)
            if result and result.get("status") == "connected":
                connected.add(sid)
        return connected

    @staticmethod
    def cached_enabled_ids(server_ids: set[str]) -> set[str]:
        """Cached-enabled servers (no transport): the Member call boundary."""
        live: set[str] = set()
        for sid in server_ids:
            server = store.get_mcp_server(sid)
            if server is None or not server["enabled"]:
                continue
            items = store.list_mcp_items(sid, kind="tool")
            if any(i.get("enabled") for i in items):
                live.add(sid)
        return live

    def cached_ids_for_agent(self, agent_id: str) -> set[str]:
        """Servers backing at least one tool enabled for ``agent_id``.

        Cached catalog only — no transport is started. Used to offer
        Member schemas for exactly the callable backend (per-call
        sandbox), never offered-then-denied.
        """
        try:
            wanted = store.list_mcp_server_ids_for_agent(agent_id)
        except Exception:
            return set()
        return self.cached_enabled_ids(set(wanted))

    async def _dispatch(self, server_id: str, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        """Run ``fn(session)`` for ``server_id``, retrying a dead session once."""
        connected = await self.ensure_for_servers({server_id})
        if server_id not in connected:
            status = store.get_mcp_server(server_id) or {}
            reason = status.get("status_message") or status.get("status") or "not connected"
            raise ValueError(f"MCP server not connected: {server_id} ({reason})")
        live = self._live.get(server_id)
        if live is None:
            raise ValueError(f"MCP server not connected: {server_id}")
        try:
            return await fn(live.session)
        except Exception as exc:
            # Session died mid-call (closed pipe, dropped HTTP connection) —
            # drop it and retry exactly once against a fresh connection.
            await self._close_live(server_id)
            store.set_mcp_status(server_id, "error", _bounded_error(exc))
            reconnected = await self.ensure_for_servers({server_id})
            if server_id not in reconnected:
                raise
            live = self._live.get(server_id)
            if live is None:
                raise
            return await fn(live.session)

    @staticmethod
    def _require_external_admin():
        from app.runtime.access import current_execution, AccessUnavailable
        if current_execution(required=False) is None:
            # HTTP management path (no bound turn): the route's own Admin
            # gate applies (Members get HTTP 403 via the API perimeter).
            # Bound turns always enforce below.
            return
        current = store.access.require_admin_action(current_execution())
        if current.execution_mode != "unrestricted":
            raise AccessUnavailable("MCP service has no restricted resource boundary")

    # -- capability calls ----------------------------------------------------

    async def call_tool(self, runtime_id: str, arguments: dict[str, Any]) -> str:
        from app.runtime.access import current_execution
        from app.runtime.policy import authorize_tool
        from app.runtime.access import AccessDenied
        try:
            authorize_tool(runtime_id, arguments)
        except AccessDenied as exc:
            return f"Error: {exc}"
        context = current_execution(required=False)
        parsed = split_runtime_tool_id(runtime_id)
        if parsed is None:
            return f"Error: not an MCP tool id: {runtime_id}"
        server_id, _tool_part = parsed
        item = store.get_mcp_item_by_runtime_id(runtime_id)
        if item is None or item["kind"] != "tool":
            return f"Error: unknown MCP tool: {runtime_id}"
        if context is not None and context.role != "admin":
            # Members never share the host session: per-chat container for
            # stdio, sanitized one-shot session for HTTP. Secrets are not
            # loaded into this path at all (see include_secrets=False).
            server = store.get_mcp_server(server_id)
            if server is None or not server["enabled"]:
                return f"Error: MCP server disabled or missing: {server_id}"
            if not item["enabled"]:
                return f"Error: MCP tool disabled: {runtime_id}"
            try:
                if server.get("transport") == "streamable_http":
                    from app.runtime.mcp.member_sandbox import member_http_call

                    return await member_http_call(
                        context, server, item, arguments or {},
                        session_factory=self.session_factory,
                    )
                return await _to_thread_sandboxed_stdio(
                    context, server, item, arguments or {}
                )
            except Exception as exc:
                from app.runtime.access import AccessDenied as _Denied
                from app.runtime.access import AccessUnavailable as _Unavailable

                if isinstance(exc, (_Denied, _Unavailable)):
                    return f"Error: {exc}"
                return "Error: Member tool execution failed"
        server = store.get_mcp_server(server_id)
        if server is None or not server["enabled"]:
            return f"Error: MCP server disabled or missing: {server_id}"
        if not item["enabled"]:
            return f"Error: MCP tool disabled: {runtime_id}"
        try:
            result = await self._dispatch(
                server_id, lambda session: session.call_tool(item["name"], arguments or {})
            )
        except Exception as exc:
            return f"Error: MCP tool call failed: {_bounded_error(exc)}"
        return render_tool_result(result)

    async def read_resource(self, server_id: str, uri: str) -> dict[str, Any]:
        self._require_external_admin()
        server = store.get_mcp_server(server_id)
        if server is None or not server["enabled"]:
            raise ValueError(f"MCP server disabled or missing: {server_id}")
        result = await self._dispatch(server_id, lambda session: session.read_resource(uri))
        return render_resource_result(result)

    async def get_prompt(
        self, server_id: str, name: str, arguments: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self._require_external_admin()
        server = store.get_mcp_server(server_id)
        if server is None or not server["enabled"]:
            raise ValueError(f"MCP server disabled or missing: {server_id}")
        result = await self._dispatch(
            server_id, lambda session: session.get_prompt(name, arguments or {})
        )
        return render_prompt_result(result)

    # -- shutdown -------------------------------------------------------------

    async def close_server(self, server_id: str) -> None:
        await self._close_live(server_id)

    async def close_all(self) -> None:
        for server_id in list(self._live.keys()):
            await self._close_live(server_id)


mcp_manager = McpConnectionManager()

__all__ = ["McpConnectionManager", "mcp_manager"]
