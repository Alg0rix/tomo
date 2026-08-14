"""Bounded conversion of MCP tool/resource/prompt results to Tomo-safe values.

Text renders as readable text; images/audio/blobs render as a bounded
type/mime/size summary rather than embedding arbitrary base64 into agent
context or HTML — content and annotations are untrusted input, never
executed or treated as instructions.
"""

from __future__ import annotations

import json
from typing import Any


def _get(obj: Any, field: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _clamp(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[truncated, {len(text)} chars total]"


def _render_content_block(block: Any) -> str:
    kind = _get(block, "type", None)
    if kind == "text":
        return str(_get(block, "text", "") or "")
    if kind == "resource_link":
        uri = str(_get(block, "uri", "") or "")
        name = str(_get(block, "name", "") or uri)
        return f"[resource link: {name} ({uri})]"
    if kind == "image":
        mime = str(_get(block, "mimeType", "") or "image")
        data = _get(block, "data", "") or ""
        return f"[image: {mime}, {len(data)} base64 chars — not embedded]"
    if kind == "audio":
        mime = str(_get(block, "mimeType", "") or "audio")
        data = _get(block, "data", "") or ""
        return f"[audio: {mime}, {len(data)} base64 chars — not embedded]"
    if kind == "resource":
        res = _get(block, "resource", None)
        uri = str(_get(res, "uri", "") or "") if res is not None else ""
        mime = str(_get(res, "mimeType", "") or "") if res is not None else ""
        text = _get(res, "text", None) if res is not None else None
        if text is not None:
            return str(text)
        blob = _get(res, "blob", None) if res is not None else None
        size = f", {len(blob)} base64 chars" if blob else ""
        return f"[embedded resource: {uri} ({mime}){size} — not embedded]"
    return f"[unsupported content block: {kind!r}]"


def render_tool_result(result: Any, *, max_chars: int = 20_000) -> str:
    parts: list[str] = []
    if _get(result, "isError", False):
        parts.append("Error: MCP tool call reported failure.")
    for block in _get(result, "content", None) or []:
        rendered = _render_content_block(block)
        if rendered:
            parts.append(rendered)
    structured = _get(result, "structuredContent", None)
    if structured:
        try:
            parts.append(json.dumps(structured, indent=2, default=str))
        except (TypeError, ValueError):
            parts.append(str(structured))
    text = "\n\n".join(p for p in parts if p) or "(no content returned)"
    return _clamp(text, max_chars)


def render_resource_result(result: Any, *, max_chars: int = 20_000) -> dict[str, Any]:
    out_contents: list[dict[str, Any]] = []
    for c in _get(result, "contents", None) or []:
        uri = str(_get(c, "uri", "") or "")
        mime = str(_get(c, "mimeType", "") or "")
        text = _get(c, "text", None)
        if text is not None:
            text_s = str(text)
            out_contents.append(
                {
                    "uri": uri,
                    "mime_type": mime,
                    "kind": "text",
                    "text": _clamp(text_s, max_chars),
                    "truncated": len(text_s) > max_chars,
                }
            )
            continue
        blob = _get(c, "blob", None)
        out_contents.append(
            {
                "uri": uri,
                "mime_type": mime,
                "kind": "blob",
                "size_base64_chars": len(blob or ""),
            }
        )
    return {"contents": out_contents}


def render_prompt_result(result: Any, *, max_chars: int = 20_000) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    for m in _get(result, "messages", None) or []:
        role = str(_get(m, "role", "user") or "user")
        content = _get(m, "content", None)
        text = _render_content_block(content) if content is not None else ""
        messages.append({"role": role, "text": _clamp(text, max_chars)})
    return {
        "description": str(_get(result, "description", "") or ""),
        "messages": messages,
    }


__all__ = ["render_tool_result", "render_resource_result", "render_prompt_result"]
