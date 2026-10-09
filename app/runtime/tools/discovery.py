"""Turn-local discovery over the already authorized tool catalog.

Enablement is the permission ceiling; loading only controls model context.
No global cache holds another chat's catalog or discoveries.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
import json
import re
from typing import Any

from app.runtime.llm.prompt_cache import stable_tools

CORE_TOOLS = frozenset({
    "bash", "read_file", "patch", "search_files", "list_dir", "list_workplaces",
    "clarify", "list_skills", "use_skill", "delegate", "start_swarm", "todo",
    "memory", "search_tools",
})
DEFAULT_TARGET_PERCENT = 5.0
GUIDANCE = (
    "\n\n## Tool discovery\n"
    "Only core tools and previously discovered tools are loaded. Other enabled "
    "capabilities are available through search_tools. Before concluding a capability "
    "is unavailable, search by task, plugin name, or tool name. Search loads matching "
    "schemas for your next response; wait for its result before calling them. "
    "Use query='*' with offset to browse. Use specific queries to keep context small. "
    "Search reports total schema tokens against a soft context target. Exceeding "
    "the target does not block required tools; keep further searches focused."
)


def _name(schema: dict) -> str:
    return schema.get("function", {}).get("name", "")


def _terms(text: str) -> set[str]:
    # Treat namespaces/underscores as word boundaries and normalize plurals.
    return {word.rstrip("s") for word in re.findall(r"[^\W_]+", text.casefold())}


class ToolDiscovery:
    def __init__(self, schemas: list[dict], *, context_window: int | None = None,
                 target_percent: float = DEFAULT_TARGET_PERCENT):
        self.catalog = {_name(s): s for s in schemas if _name(s)}
        self.loaded = set(self.catalog) & CORE_TOOLS
        self.context_window = context_window
        self.target_percent = target_percent

    def budget_status(self) -> dict[str, int | bool | None]:
        """Estimate all loaded schemas, including core; never deny loading."""
        size = len(json.dumps([self.catalog[n] for n in sorted(self.loaded)],
                              ensure_ascii=False).encode("utf-8"))
        tokens = (size + 3) // 4
        target = (max(1, int(self.context_window * self.target_percent / 100))
                  if self.context_window else None)
        return {"schema_tokens": tokens, "schema_target_tokens": target,
                "over_target": target is not None and tokens > target}

    def _available(self, names: set[str] | None = None) -> dict[str, dict]:
        from app.runtime.access import current_execution
        from app.runtime.policy import filter_schemas
        from app.services import store

        context = store.access.revalidate(current_execution())
        candidates = names if names is not None else self.catalog.keys()
        ceiling = [self.catalog[name] for name in candidates if name in context.tool_ids]
        return {_name(s): s for s in filter_schemas(context, ceiling)}

    def _load(self, name: str) -> None:
        self.loaded.add(name)

    def restore(self, messages: list[dict]) -> None:
        """Restore only paired discovery results/calls from this agent's history.

        Names are intersected with the current authorized catalog; history never
        grants permission. A compacted trail naturally starts a fresh tool set.
        """
        calls: dict[str, str] = {}
        retained: list[str] = []
        for message in messages:
            for call in message.get("tool_calls") or []:
                name = call.get("function", {}).get("name", "")
                calls[call.get("id", "")] = name
                if name in self.catalog:
                    retained.append(name)
            if message.get("role") != "tool":
                continue
            if calls.get(message.get("tool_call_id")) != "search_tools":
                continue
            try:
                result = json.loads(message.get("content") or "")
            except (ValueError, TypeError):
                continue
            names = result.get("loaded_tools", []) if isinstance(result, dict) else []
            if not isinstance(names, list):
                continue
            for name in names:
                if isinstance(name, str) and name in self.catalog:
                    retained.append(name)
        # Retain loading history for cache reuse. A soft target never evicts a
        # tool needed by the task; compaction and permission changes remove it.
        for name in retained:
            self._load(name)

    def schemas(self) -> list[dict]:
        available = self._available(self.loaded)
        self.loaded.intersection_update(available)
        return stable_tools([available[n] for n in sorted(self.loaded) if n in available]) or []

    def search(self, arguments: dict[str, Any]) -> str:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            return "Error: query must be a non-empty string of at most 512 characters"
        limit, offset = arguments.get("limit", 3), arguments.get("offset", 0)
        if (type(limit) is not int or not 1 <= limit <= 5
                or type(offset) is not int or offset < 0):
            return "Error: limit must be 1–5 and offset must be a non-negative integer"
        available = self._available()
        self.loaded.intersection_update(available)
        terms = _terms(query)
        ranked = []
        for name, schema in available.items():
            if name == "search_tools":
                continue
            description = schema["function"].get("description", "")
            score = (4 * len(terms & _terms(name))
                     + len(terms & _terms(description)))
            if name.casefold() == query.strip().casefold():
                score += 100
            if query.strip() == "*" or score:
                ranked.append((-score, name, description))
        ranked.sort()
        results, loaded = [], []
        for _, name, description in ranked[offset:offset + limit]:
            self._load(name)
            loaded.append(name)
            results.append({"name": name, "description": description[:400], "loaded": True})
        return json.dumps({
            "results": results, "loaded_tools": loaded, "total_matches": len(ranked),
            "next_offset": offset + limit if offset + limit < len(ranked) else None,
            **self.budget_status(),
        }, ensure_ascii=False)


_current: ContextVar[ToolDiscovery | None] = ContextVar("tool_discovery", default=None)


def native_tool_layout(messages: list[dict], schemas: list[dict] | None) -> tuple[list[dict], list[dict] | None]:
    """Append definitions at their discovery position on supporting providers.

    Only schemas admitted by the caller may be injected. Historical tool text
    supplies positions/names, never definitions or permission. Rebuilding from
    the trail keeps positions stable across client recreation and follow-ups.
    """
    if _current.get() is None or not schemas:
        return messages, schemas
    catalog = {_name(s): s for s in schemas}
    offered = set(catalog) & CORE_TOOLS
    if not offered:
        # Inline Claude definitions require at least one initial non-deferred
        # tool. If all core tools were revoked, use the portable representation.
        return messages, schemas
    initial = [catalog[n] for n in sorted(offered)]
    laid_out: list[dict] = []
    calls: dict[str, str] = {}

    def append_tools(names: list[str]) -> None:
        additions = sorted({n for n in names if n in catalog and n not in offered})
        if additions:
            laid_out.append({"type": "additional_tools", "role": "developer",
                             "tools": [catalog[n] for n in additions]})
            offered.update(additions)

    for message in messages:
        requested = []
        for call in message.get("tool_calls") or []:
            name = call.get("function", {}).get("name", "")
            calls[call.get("id", "")] = name
            requested.append(name)
        # A historical direct call can precede a discovery record (older chats).
        append_tools(requested)
        laid_out.append(message)
        if (message.get("role") == "tool"
                and calls.get(message.get("tool_call_id")) == "search_tools"):
            try:
                result = json.loads(message.get("content") or "")
            except (ValueError, TypeError):
                continue
            names = result.get("loaded_tools") if isinstance(result, dict) else None
            if isinstance(names, list):
                append_tools([n for n in names if isinstance(n, str)])
    # Working-memory compaction can remove discovery records mid-turn.
    append_tools(list(catalog))
    return laid_out, initial


def bind(discovery: ToolDiscovery | None) -> Token[ToolDiscovery | None]:
    return _current.set(discovery)


def reset(token: Token[ToolDiscovery | None]) -> None:
    _current.reset(token)


def run(arguments: dict[str, Any]) -> str:
    discovery = _current.get()
    if discovery is None:
        return "Error: tool discovery is unavailable in this execution scope"
    return discovery.search(arguments)
