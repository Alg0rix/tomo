"""SSE formatting and loop-event → wire/history mapping."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("app.channels.web")

_PREVIEW = 80


def sse_summary(name: str, data: dict[str, Any]) -> str:
    """Compact one-line summary for an SSE payload (deltas truncated)."""
    if name == "delta":
        content = data.get("content") or ""
        return f"chars={len(content)} preview={content[:_PREVIEW]!r}"
    if name == "done":
        content = data.get("content") or ""
        return (
            f"turn_id={data.get('turn_id')} agent_id={data.get('agent_id')} "
            f"chars={len(content)} preview={content[:_PREVIEW]!r}"
        )
    if name == "thinking":
        content = data.get("content") or ""
        return f"chars={len(content)} preview={content[:_PREVIEW]!r}"
    if name == "tool":
        return f"tool={data.get('tool')!r} args={data.get('args')!r}"
    if name == "tool_result":
        result = data.get("result")
        preview = result if isinstance(result, str) else repr(result)
        return (
            f"tool={data.get('tool')!r} error={data.get('error')} "
            f"result={str(preview)[:_PREVIEW]!r}"
        )
    if name == "session":
        return f"session_id={data.get('session_id')} title={data.get('title')!r}"
    if name == "state":
        return f"agent_id={data.get('agent_id')} busy={data.get('busy')}"
    if name == "turn.start":
        return (
            f"turn_id={data.get('turn_id')} agent={data.get('agent')!r} "
            f"agent_id={data.get('agent_id')} delegate={data.get('delegate')}"
        )
    if name == "error":
        return f"agent_id={data.get('agent_id')} message={data.get('message')!r}"
    if name == "delegate":
        return (
            f"from={data.get('from')!r} to={data.get('to')!r} "
            f"reason={data.get('reason')!r} "
            f"content={str(data.get('content') or '')[:_PREVIEW]!r}"
        )
    if name == "heartbeat":
        return "ok"
    try:
        return json.dumps(data, separators=(",", ":"))[:200]
    except TypeError:
        return repr(data)[:200]


def fmt_sse(event: dict[str, Any]) -> str:
    """Serialise one SSE event to the ``event:``/``data:``/``id:`` wire form."""
    name = event["event"]
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {"value": data}
    seq = event.get("seq")
    logger.info(
        "sse event=%s seq=%s %s",
        name,
        seq,
        sse_summary(name, data),
    )
    payload = json.dumps(data, separators=(",", ":"))
    lines = [f"event: {name}", f"data: {payload}"]
    if seq is not None:
        lines.append(f"id: {seq}")
    return "\n".join(lines) + "\n\n"


def now() -> float:
    return time.time()


def map_loop_event(
    ev: dict[str, Any],
    agent_id: str,
    agent_name: str,
    seq: int,
    turn_id: str,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Map one loop event to ``(sse_chunks, history_entries, next_seq)``.

    ``delegate`` kind is ignored here — the turn orchestrator handles handoff.
    """
    kind = ev["kind"]
    chunks: list[str] = []
    entries: list[dict[str, Any]] = []

    if kind == "thinking":
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "thinking",
                    "data": {"content": ev["content"], "agent_id": agent_id},
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "thinking",
                "content": ev["content"],
                "agent_id": agent_id,
                "ts": now(),
            }
        )
    elif kind == "delta":
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "delta",
                    "data": {"content": ev.get("content") or "", "agent_id": agent_id},
                    "seq": seq,
                }
            )
        )
    elif kind == "tool":
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "tool",
                    "data": {
                        "tool": ev["tool"],
                        "args": ev["args"],
                        "agent_id": agent_id,
                    },
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "tool_call",
                "function": ev["tool"],
                "params": ev["args"],
                "agent_id": agent_id,
                "ts": now(),
            }
        )
    elif kind == "tool_result":
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "tool_result",
                    "data": {
                        "tool": ev["tool"],
                        "result": ev["result"],
                        "error": ev["error"],
                        "agent_id": agent_id,
                    },
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "tool_output",
                "content": ev["result"],
                "function": ev["tool"],
                "error": ev["error"],
                "agent_id": agent_id,
                "ts": now(),
            }
        )
    elif kind == "final":
        content = ev["content"] or ""
        if not ev.get("already_streamed"):
            seq += 1
            chunks.append(
                fmt_sse(
                    {
                        "event": "delta",
                        "data": {"content": content, "agent_id": agent_id},
                        "seq": seq,
                    }
                )
            )
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "done",
                    "data": {
                        "turn_id": turn_id,
                        "content": content,
                        "agent": agent_name,
                        "agent_id": agent_id,
                    },
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "final",
                "content": content,
                "agent_id": agent_id,
                "ts": now(),
            }
        )
    elif kind == "error":
        msg = ev["message"]
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "error",
                    "data": {"message": msg, "agent_id": agent_id},
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "error",
                "content": msg,
                "agent_id": agent_id,
                "ts": now(),
            }
        )

    return chunks, entries, seq


__all__ = ["fmt_sse", "map_loop_event", "now", "sse_summary"]
