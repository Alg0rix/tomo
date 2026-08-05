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
    if name == "approval_required":
        return f"id={data.get('id')} tool={data.get('tool')!r}"
    if name == "clarify_required":
        return f"id={data.get('id')} q={str(data.get('question') or '')[:_PREVIEW]!r}"
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
            f"task={str(data.get('task') or '')[:_PREVIEW]!r} "
            f"parallel={data.get('parallel_index')}/{data.get('parallel_total')} "
            f"dcid={data.get('delegate_call_id') or '-'}"
        )
    if name == "subagent_start":
        return (
            f"agent_id={data.get('agent_id')} task={str(data.get('task') or '')[:_PREVIEW]!r} "
            f"parallel={data.get('parallel_index')}/{data.get('parallel_total')} "
            f"dcid={data.get('delegate_call_id') or '-'}"
        )
    if name == "subagent_done":
        content = data.get("content") or ""
        return (
            f"agent_id={data.get('agent_id')} status={data.get('status')} "
            f"dcid={data.get('delegate_call_id') or '-'} "
            f"chars={len(content)} preview={content[:_PREVIEW]!r}"
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


def session_busy_sse(
    *,
    agent_id: str = "",
    session_id: str = "",
    seq: int = 1,
) -> str:
    """Wire event when a session already has an in-flight turn (client re-queues)."""
    return fmt_sse(
        {
            "event": "error",
            "data": {
                "message": (
                    "Session is busy with another turn. "
                    "Your message was not accepted — try again when idle."
                ),
                "code": "session_busy",
                "agent_id": agent_id,
                "session_id": session_id,
            },
            "seq": seq,
        }
    )


def now() -> float:
    return time.time()


def _delegate_call_id(ev: dict[str, Any]) -> str:
    """Parent ``delegate`` tool call id used to disambiguate same-agent runs."""
    raw = ev.get("delegate_call_id") or ""
    return raw if isinstance(raw, str) else ""


def _stamp_dcid(data: dict[str, Any], ev: dict[str, Any]) -> dict[str, Any]:
    """Attach ``delegate_call_id`` (+ parallel slot) when present."""
    dcid = _delegate_call_id(ev)
    if dcid:
        data["delegate_call_id"] = dcid
    if "parallel_index" in ev and "parallel_index" not in data:
        data["parallel_index"] = ev.get("parallel_index")
    if "parallel_total" in ev and "parallel_total" not in data:
        data["parallel_total"] = ev.get("parallel_total")
    return data


def map_loop_event(
    ev: dict[str, Any],
    agent_id: str,
    agent_name: str,
    seq: int,
    turn_id: str,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Map one loop event to ``(sse_chunks, history_entries, next_seq)``.

    Handles the full event vocabulary including subagent delegation
    (``delegate``, ``subagent_final``) and ATG meta-events. Nested subagent
    events carry their own ``agent_id``; the caller resolves attribution.
    Parallel (or sequential) runs of the same catalog agent share
    ``agent_id`` but are distinguished by ``delegate_call_id``.
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
                    "data": _stamp_dcid(
                        {
                            "content": ev["content"],
                            "agent_id": agent_id,
                            "agent": agent_name,
                        },
                        ev,
                    ),
                    "seq": seq,
                }
            )
        )
        entries.append(
            _stamp_dcid(
                {
                    "type": "thinking",
                    "content": ev["content"],
                    "agent_id": agent_id,
                    "ts": now(),
                },
                ev,
            )
        )
    elif kind == "delta":
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "delta",
                    "data": _stamp_dcid(
                        {
                            "content": ev.get("content") or "",
                            "agent_id": agent_id,
                            "agent": agent_name,
                        },
                        ev,
                    ),
                    "seq": seq,
                }
            )
        )
    elif kind == "tool":
        seq += 1
        call_id = ev.get("call_id") or ""
        tool_data = _stamp_dcid(
            {
                "tool": ev["tool"],
                "args": ev["args"],
                "agent_id": agent_id,
                "agent": agent_name,
            },
            ev,
        )
        if call_id:
            tool_data["call_id"] = call_id
        chunks.append(
            fmt_sse(
                {
                    "event": "tool",
                    "data": tool_data,
                    "seq": seq,
                }
            )
        )
        entry = _stamp_dcid(
            {
                "type": "tool_call",
                "function": ev["tool"],
                "params": ev["args"],
                "agent_id": agent_id,
                "ts": now(),
            },
            ev,
        )
        if call_id:
            entry["call_id"] = call_id
        entries.append(entry)
    elif kind == "tool_result":
        seq += 1
        call_id = ev.get("call_id") or ""
        data = _stamp_dcid(
            {
                "tool": ev["tool"],
                "result": ev["result"],
                "error": ev["error"],
                "agent_id": agent_id,
                "agent": agent_name,
            },
            ev,
        )
        if call_id:
            data["call_id"] = call_id
        if ev.get("todos") is not None:
            data["todos"] = ev["todos"]
        chunks.append(
            fmt_sse(
                {
                    "event": "tool_result",
                    "data": data,
                    "seq": seq,
                }
            )
        )
        entry = _stamp_dcid(
            {
                "type": "tool_output",
                "content": ev["result"],
                "function": ev["tool"],
                "error": ev["error"],
                "agent_id": agent_id,
                "ts": now(),
            },
            ev,
        )
        if call_id:
            entry["call_id"] = call_id
        entries.append(entry)
        # Also emit a dedicated todos SSE when the tool carried a snapshot.
        if isinstance(ev.get("todos"), list):
            seq += 1
            chunks.append(
                fmt_sse(
                    {
                        "event": "todos",
                        "data": _stamp_dcid(
                            {
                                "todos": ev["todos"],
                                "source": "tool",
                                "agent_id": agent_id,
                                "agent": agent_name,
                            },
                            ev,
                        ),
                        "seq": seq,
                    }
                )
            )
    elif kind == "final":
        content = ev["content"] or ""
        # Models sometimes echo internal swarm notes; never show/persist those.
        if content.lstrip().startswith("[Swarm]"):
            content = ""
        if not ev.get("already_streamed"):
            if content:
                seq += 1
                chunks.append(
                    fmt_sse(
                        {
                            "event": "delta",
                            "data": _stamp_dcid(
                                {
                                    "content": content,
                                    "agent_id": agent_id,
                                    "agent": agent_name,
                                },
                                ev,
                            ),
                            "seq": seq,
                        }
                    )
                )
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "done",
                    "data": _stamp_dcid(
                        {
                            "turn_id": turn_id,
                            "content": content,
                            "agent": agent_name,
                            "agent_id": agent_id,
                        },
                        ev,
                    ),
                    "seq": seq,
                }
            )
        )
        if content.strip():
            entries.append(
                _stamp_dcid(
                    {
                        "type": "final",
                        "content": content,
                        "agent_id": agent_id,
                        "ts": now(),
                    },
                    ev,
                )
            )
    elif kind == "error":
        msg = ev["message"]
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "error",
                    "data": _stamp_dcid(
                        {
                            "message": msg,
                            "agent_id": agent_id,
                            "agent": agent_name,
                        },
                        ev,
                    ),
                    "seq": seq,
                }
            )
        )
        entries.append(
            _stamp_dcid(
                {
                    "type": "error",
                    "content": msg,
                    "agent_id": agent_id,
                    "ts": now(),
                },
                ev,
            )
        )
    elif kind == "delegate":
        from_id = ev.get("from") or ""
        to_id = ev.get("to") or ""
        reason = ev.get("reason") or "delegate"
        task = ev.get("task") or reason
        to_name = ev.get("to_name") or to_id
        parallel_index = ev.get("parallel_index", 1)
        parallel_total = ev.get("parallel_total", 1)
        data = _stamp_dcid(
            {
                "from": from_id,
                "to": to_id,
                "reason": reason,
                "task": task,
                "agent_id": to_id,
                "agent": to_name,
                "parallel_index": parallel_index,
                "parallel_total": parallel_total,
                "content": f"Handing off to {to_name}",
            },
            ev,
        )
        seq += 1
        chunks.append(
            fmt_sse({"event": "delegate", "data": data, "seq": seq})
        )
        params = {
            "from": from_id,
            "to": to_id,
            "reason": reason,
            "task": task,
            "to_name": to_name,
            "parallel_index": parallel_index,
            "parallel_total": parallel_total,
        }
        _stamp_dcid(params, ev)
        entries.append(
            {
                "type": "delegate",
                "content": data["content"],
                "agent_id": to_id,
                "params": params,
                "ts": now(),
            }
        )
    elif kind == "subagent_start":
        sa_id = ev.get("agent_id") or agent_id
        sa_name = agent_name
        task = ev.get("task") or ""
        parallel_index = ev.get("parallel_index", 1)
        parallel_total = ev.get("parallel_total", 1)
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "subagent_start",
                    "data": _stamp_dcid(
                        {
                            "agent_id": sa_id,
                            "agent": sa_name,
                            "task": task,
                            "parallel_index": parallel_index,
                            "parallel_total": parallel_total,
                        },
                        ev,
                    ),
                    "seq": seq,
                }
            )
        )
        params = {
            "name": sa_name,
            "task": task,
            "parallel_index": parallel_index,
            "parallel_total": parallel_total,
        }
        _stamp_dcid(params, ev)
        entries.append(
            {
                "type": "subagent_start",
                "content": "",
                "agent_id": sa_id,
                "params": params,
                "ts": now(),
            }
        )
    elif kind == "subagent_done":
        sa_id = ev.get("agent_id") or agent_id
        sa_name = agent_name
        content = ev.get("content") or ""
        status = ev.get("status", "ok")
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "subagent_done",
                    "data": _stamp_dcid(
                        {
                            "agent_id": sa_id,
                            "agent": sa_name,
                            "content": content[:200],
                            "status": status,
                        },
                        ev,
                    ),
                    "seq": seq,
                }
            )
        )
        params = {
            "name": sa_name,
            "status": status,
        }
        _stamp_dcid(params, ev)
        entries.append(
            {
                "type": "subagent_done",
                "content": content[:200],
                "agent_id": sa_id,
                "params": params,
                "ts": now(),
            }
        )
    elif kind == "subagent_final":
        content = ev.get("content") or ""
        if content:
            seq += 1
            chunks.append(
                fmt_sse(
                    {
                        "event": "delta",
                        "data": _stamp_dcid(
                            {
                                "content": content,
                                "agent_id": agent_id,
                                "agent": agent_name,
                            },
                            ev,
                        ),
                        "seq": seq,
                    }
                )
            )
        if content.strip():
            entries.append(
                _stamp_dcid(
                    {
                        "type": "final",
                        "content": content,
                        "agent_id": agent_id,
                        "ts": now(),
                    },
                    ev,
                )
            )
    elif kind == "subagent_error":
        msg = ev.get("message", "subagent error")
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "error",
                    "data": _stamp_dcid(
                        {
                            "message": msg,
                            "agent_id": agent_id,
                            "agent": agent_name,
                        },
                        ev,
                    ),
                    "seq": seq,
                }
            )
        )
    elif kind == "approval_required":
        seq += 1
        data = _stamp_dcid(
            {
                "id": ev.get("id"),
                "tool": ev.get("tool"),
                "args_preview": ev.get("args_preview"),
                "findings": ev.get("findings") or [],
                "description": ev.get("description") or "",
                "choices": ev.get("choices") or ["once", "session", "always", "deny"],
                "allow_permanent": ev.get("allow_permanent", True),
                "allow_session": ev.get("allow_session", True),
                "smart_denied": ev.get("smart_denied", False),
                "agent_id": agent_id,
                "agent": agent_name,
            },
            ev,
        )
        chunks.append(
            fmt_sse({"event": "approval_required", "data": data, "seq": seq})
        )
    elif kind == "clarify_required":
        seq += 1
        data = _stamp_dcid(
            {
                "id": ev.get("id"),
                "question": ev.get("question") or "",
                "choices": ev.get("choices") or [],
                "agent_id": agent_id,
                "agent": agent_name,
            },
            ev,
        )
        chunks.append(
            fmt_sse({"event": "clarify_required", "data": data, "seq": seq})
        )
    elif kind == "steer":
        # Mid-turn user injection — persist + broadcast so reconnect/history see it.
        content = ev.get("content") or ""
        att_ids = list(ev.get("attachment_ids") or [])
        att_meta = list(ev.get("attachments") or [])
        seq += 1
        data = {
            "content": content,
            "steered": True,
            "session_id": ev.get("session_id") or "",
        }
        if att_ids:
            data["attachment_ids"] = att_ids
            data["attachments"] = att_meta
        chunks.append(fmt_sse({"event": "user", "data": data, "seq": seq}))
        entry: dict[str, Any] = {
            "type": "user",
            "content": content,
            "ts": now(),
            "steered": True,
        }
        if att_ids:
            entry["attachment_ids"] = att_ids
            entry["attachments"] = att_meta
        entries.append(entry)
    elif kind == "todos":
        seq += 1
        chunks.append(
            fmt_sse(
                {
                    "event": "todos",
                    "data": _stamp_dcid(
                        {
                            "todos": ev.get("todos") or [],
                            "summary": ev.get("summary") or {},
                            "source": ev.get("source") or "atg",
                            "agent_id": agent_id,
                            "agent": agent_name,
                        },
                        ev,
                    ),
                    "seq": seq,
                }
            )
        )
    elif kind in ("atg_wave", "atg_summary"):
        # Meta-events: ATG tool/tool_result map normally; todos carry the plan UI.
        pass

    return chunks, entries, seq


__all__ = ["fmt_sse", "map_loop_event", "now", "session_busy_sse", "sse_summary"]
