"""Opaque-cursor pagination and MCP capability -> ``mcp_items`` row normalization.

Normalization never executes or trusts server-declared safety hints —
annotations, titles, and descriptions are display metadata only. Tool input
schemas become an OpenAI function-tool schema so Task 6's catalog merge can
hand the ``schema`` value straight to ``tools=`` without re-deriving it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.runtime.mcp.names import runtime_tool_id

_MAX_ITEMS_PER_FAMILY = 10_000


def _get(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


async def paginate_mcp_list(
    fetch_page: Callable[[str | None], Awaitable[Any]], field: str
) -> list[Any]:
    """Follow ``nextCursor`` pages from ``fetch_page`` until exhausted.

    Cursors are opaque server tokens — never inspected, only round-tripped.
    A missing/empty cursor stops pagination; the total is capped so a
    misbehaving server cannot force unbounded memory growth.
    """
    items: list[Any] = []
    cursor: str | None = None
    while True:
        page = await fetch_page(cursor)
        values = _get(page, field, None) or []
        items.extend(values)
        if len(items) >= _MAX_ITEMS_PER_FAMILY:
            return items[:_MAX_ITEMS_PER_FAMILY]
        cursor = _get(page, "nextCursor", None)
        if not cursor:
            return items


def _meta_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json", exclude_none=True)
        except TypeError:
            return dump()
    return {}


def normalize_tool(server: dict[str, Any], raw_tool: Any) -> dict[str, Any]:
    name = str(_get(raw_tool, "name", "") or "")
    title = _get(raw_tool, "title", None)
    description = _get(raw_tool, "description", None)
    input_schema = _get(raw_tool, "inputSchema", None) or {"type": "object", "properties": {}}
    display = str(title or description or name)
    runtime_id = runtime_tool_id(server["id"], name)
    return {
        "kind": "tool",
        "runtime_id": runtime_id,
        "name": name,
        "title": str(title or name),
        "description": display,
        "uri": "",
        "mime_type": "",
        "schema": {
            "type": "function",
            "function": {
                "name": runtime_id,
                "description": display,
                "parameters": input_schema,
            },
        },
        "metadata": {
            "mcp_name": name,
            "server_id": server["id"],
            "output_schema": _get(raw_tool, "outputSchema", None),
            "annotations": _meta_dict(_get(raw_tool, "annotations", None)),
            "raw_meta": _meta_dict(_get(raw_tool, "meta", None)),
        },
    }


def normalize_resource(server: dict[str, Any], raw_resource: Any) -> dict[str, Any]:
    name = str(_get(raw_resource, "name", "") or "")
    uri = str(_get(raw_resource, "uri", "") or "")
    title = _get(raw_resource, "title", None)
    description = _get(raw_resource, "description", None)
    return {
        "kind": "resource",
        "runtime_id": "",
        "name": name,
        "title": str(title or name),
        "description": str(description or ""),
        "uri": uri,
        "mime_type": str(_get(raw_resource, "mimeType", "") or ""),
        "schema": {},
        "metadata": {
            "server_id": server["id"],
            "size": _get(raw_resource, "size", None),
            "annotations": _meta_dict(_get(raw_resource, "annotations", None)),
        },
    }


def normalize_resource_template(server: dict[str, Any], raw_template: Any) -> dict[str, Any]:
    name = str(_get(raw_template, "name", "") or "")
    uri_template = str(_get(raw_template, "uriTemplate", "") or "")
    title = _get(raw_template, "title", None)
    description = _get(raw_template, "description", None)
    return {
        "kind": "resource_template",
        "runtime_id": "",
        "name": name,
        "title": str(title or name),
        "description": str(description or ""),
        "uri": uri_template,
        "mime_type": str(_get(raw_template, "mimeType", "") or ""),
        "schema": {},
        "metadata": {
            "server_id": server["id"],
            "annotations": _meta_dict(_get(raw_template, "annotations", None)),
        },
    }


def normalize_prompt(server: dict[str, Any], raw_prompt: Any) -> dict[str, Any]:
    name = str(_get(raw_prompt, "name", "") or "")
    title = _get(raw_prompt, "title", None)
    description = _get(raw_prompt, "description", None)
    args = []
    for arg in _get(raw_prompt, "arguments", None) or []:
        args.append(
            {
                "name": str(_get(arg, "name", "") or ""),
                "description": str(_get(arg, "description", "") or ""),
                "required": bool(_get(arg, "required", False)),
            }
        )
    return {
        "kind": "prompt",
        "runtime_id": "",
        "name": name,
        "title": str(title or name),
        "description": str(description or ""),
        "uri": "",
        "mime_type": "",
        "schema": {"arguments": args},
        "metadata": {"server_id": server["id"]},
    }


__all__ = [
    "paginate_mcp_list",
    "normalize_tool",
    "normalize_resource",
    "normalize_resource_template",
    "normalize_prompt",
]
