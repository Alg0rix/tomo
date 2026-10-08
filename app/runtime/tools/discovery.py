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
DEFERRED_TOKEN_BUDGET = 6000
GUIDANCE = (
    "\n\n## Tool discovery\n"
    "Only core tools and previously discovered tools are loaded. Other enabled "
    "capabilities are available through search_tools. Before concluding a capability "
    "is unavailable, search by task, plugin name, or tool name. Search loads matching "
    "schemas for your next response; wait for its result before calling them. "
    "Use query='*' with offset to browse. Use specific queries to keep context small."
)


def _name(schema: dict) -> str:
    return schema.get("function", {}).get("name", "")


def _terms(text: str) -> set[str]:
    # Treat namespaces/underscores as word boundaries and normalize plurals.
    return {word.rstrip("s") for word in re.findall(r"[^\W_]+", text.casefold())}


class ToolDiscovery:
    def __init__(self, schemas: list[dict], *, token_budget: int = DEFERRED_TOKEN_BUDGET):
        self.catalog = {_name(s): s for s in schemas if _name(s)}
        self.loaded = set(self.catalog) & CORE_TOOLS
        self.token_budget = token_budget

    def _available(self, names: set[str] | None = None) -> dict[str, dict]:
        from app.runtime.access import current_execution
        from app.runtime.policy import filter_schemas
        from app.services import store

        context = store.access.revalidate(current_execution())
        candidates = names if names is not None else self.catalog.keys()
        ceiling = [self.catalog[name] for name in candidates if name in context.tool_ids]
        return {_name(s): s for s in filter_schemas(context, ceiling)}

    def _load(self, name: str) -> bool:
        if name in self.loaded:
            return True
        deferred = (self.loaded | {name}) - CORE_TOOLS
        # Approximate wire-size estimate, including schema keys/descriptions.
        size = sum(len(json.dumps(self.catalog[n], ensure_ascii=False).encode("utf-8"))
                   for n in deferred)
        if size > self.token_budget * 4:
            return False
        self.loaded.add(name)
        return True

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
        # Prefer recent work when an old conversation exceeds the budget.
        # During a running turn, only additions and permission removals change it.
        for name in reversed(retained):
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
        results, loaded, budget_limited = [], [], False
        for _, name, description in ranked[offset:offset + limit]:
            is_loaded = self._load(name)
            budget_limited |= not is_loaded
            if is_loaded:
                loaded.append(name)
            results.append({"name": name, "description": description[:400], "loaded": is_loaded})
        return json.dumps({
            "results": results, "loaded_tools": loaded, "total_matches": len(ranked),
            "next_offset": offset + limit if offset + limit < len(ranked) else None,
            "budget_limited": budget_limited,
        }, ensure_ascii=False)


_current: ContextVar[ToolDiscovery | None] = ContextVar("tool_discovery", default=None)


def bind(discovery: ToolDiscovery | None) -> Token[ToolDiscovery | None]:
    return _current.set(discovery)


def reset(token: Token[ToolDiscovery | None]) -> None:
    _current.reset(token)


def run(arguments: dict[str, Any]) -> str:
    discovery = _current.get()
    if discovery is None:
        return "Error: tool discovery is unavailable in this execution scope"
    return discovery.search(arguments)
