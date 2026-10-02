"""Session-history compaction behind ``/compact``.

Appends a ``{"type": "compact"}`` marker entry whose ``content`` is an
LLM-written summary of everything before it. History rows are never
deleted — the transcript UI still shows them and the marker renders as a
divider — but ``history_to_messages`` drops everything before the last
marker and injects the summary instead, so the next turn starts from a
short context.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MIN_SUMMARIZABLE = 6
_MAX_TRANSCRIPT_CHARS = 48_000
_ENTRY_CHARS = 600

_COMPACT_PROMPT = """You are compacting a conversation's history for an agent that keeps
chatting after this point. Summarize what happened into a brief the agent
can rely on for continuity:

- the user's goals and requests, and what was delivered
- decisions, preferences, and durable facts learned about the user
- files, artifacts, or paths created (with names)
- errors that still matter and any unfinished tasks or promised follow-ups

Be dense — every sentence must earn its tokens. Plain paragraphs or short
bullets, no preamble. Write in the conversation's language.

Transcript:
"""


def entries_after_last_compact(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """``(entries, live_start)`` — everything after the last compact marker."""
    start = 0
    for idx, entry in enumerate(history):
        if entry.get("type") == "compact":
            start = idx + 1
    return history[start:], start


def _clip(text: Any, limit: int) -> str:
    body = str(text or "").strip()
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _transcript(entries: list[dict[str, Any]]) -> tuple[str, int]:
    """Bounded plain-text transcript + count of summarizable entries."""
    lines: list[str] = []
    count = 0
    for entry in entries:
        etype = entry.get("type")
        line: str | None = None
        if etype == "user":
            line = "User: " + _clip(entry.get("content"), _ENTRY_CHARS)
        elif etype in {"final", "subagent_final"}:
            who = entry.get("agent_id") or "agent"
            line = f"Agent({who}): " + _clip(entry.get("content"), _ENTRY_CHARS)
        elif etype == "tool_call":
            line = "Tool call: " + _clip(
                f"{entry.get('function')} {entry.get('params')}", 200
            )
        elif etype == "tool_output":
            line = "Tool result: " + _clip(entry.get("content"), 240)
        elif etype == "delegate":
            line = "Handoff: " + _clip(entry.get("content"), 200)
        elif etype == "error":
            line = "Error: " + _clip(entry.get("content"), 200)
        if line:
            lines.append(line)
            count += 1
    text = "\n".join(lines)
    if len(text) > _MAX_TRANSCRIPT_CHARS:
        text = text[: _MAX_TRANSCRIPT_CHARS - 1] + "…"
    return text, count


async def compact_session(
    session_id: str, *, agent_id: str | None = None
) -> dict[str, Any]:
    """Summarize *session_id*'s history into a compact marker entry.

    Returns ``{"status", "message", "compacted"}`` — ``status`` is ``"ok"``,
    ``"too_short"``, ``"no_history"``, or ``"no_model"``. Raises only on
    unexpected store/LLM failures; the caller reports the exception.
    """
    from app.services import store

    history = store.get_session_history(session_id)
    if not history:
        return {
            "status": "no_history",
            "message": "Nothing to compact yet — start chatting first.",
            "compacted": 0,
        }
    segment, _ = entries_after_last_compact(history)
    transcript, count = _transcript(segment)
    if count < _MIN_SUMMARIZABLE:
        return {
            "status": "too_short",
            "message": "The conversation is still short — nothing worth compacting.",
            "compacted": 0,
        }

    from app.runtime.llm import get_auxiliary_llm

    try:
        client = get_auxiliary_llm('compaction', agent_id=agent_id, session_id=session_id)
    except Exception:
        return {
            "status": "no_model",
            "message": "Compaction needs a configured model profile (System → Models).",
            "compacted": 0,
        }
    resp = await client.complete(
        [{"role": "user", "content": _COMPACT_PROMPT + transcript}]
    )
    summary = (resp.content or "").strip()
    if not summary:
        return {
            "status": "error",
            "message": "The model returned an empty summary — history left untouched.",
            "compacted": 0,
        }

    from app.channels.sse_map import now

    store.append_session_history(
        session_id,
        {
            "type": "compact",
            "content": summary,
            "agent_id": agent_id or "",
            "ts": now(),
            "params": {"compacted": count},
        },
    )
    return {
        "status": "ok",
        "message": (
            f"Compacted {count} earlier messages into a summary. "
            "I continue with that summary as context — the full chat stays "
            "visible in the transcript."
        ),
        "compacted": count,
        "summary": summary,
    }


async def maybe_auto_compact(
    session_id: str, *, agent_id: str | None = None
) -> dict[str, Any] | None:
    """Compact *session_id* when its projected context crosses the threshold.

    Settings: ``auto_compact_enabled`` (default on), ``auto_compact_threshold``
    (fraction of the model's context window, default 0.9). Returns the
    ``compact_session`` result with ``auto=True``, or None when nothing was
    done — including when compaction wouldn't help (already-compacted tail or
    a too-short segment).
    """
    from app.services import store

    settings = store.get_settings()
    if not settings.get("auto_compact_enabled", True):
        return None
    history = store.get_session_history(session_id)
    live, _ = entries_after_last_compact(history or [])
    if not live:
        return None

    from app.runtime.agent.context_usage import (
        compute_context_usage,
        estimate_tokens,
        _resolve_context_limit,
    )

    limit = _resolve_context_limit(agent_id, session_id=session_id)
    if limit is None:
        return None
    threshold = float(settings.get("auto_compact_threshold") or 0.9)
    threshold = min(max(threshold, 0.5), 0.99)
    # Cheap gate first — rough live-segment size before paying for the full
    # projection (system prompt + tool schemas) on every turn.
    rough = sum(
        estimate_tokens(str(e.get("content") or ""))
        + estimate_tokens(str(e.get("params") or ""))
        for e in live
    )
    if rough < limit * max(0.4, threshold - 0.25):
        return None
    usage = compute_context_usage(agent_id, history, limit=limit, session_id=session_id)
    if usage["used"] < usage["limit"] * threshold:
        return None
    result = await compact_session(session_id, agent_id=agent_id)
    if result["status"] != "ok":
        return None
    result["auto"] = True
    result["usage_before"] = {
        "used": usage["used"],
        "limit": usage["limit"],
        "percent": usage["percent"],
    }
    logger.info(
        "auto-compact session=%s compacted=%s used=%s/%s",
        session_id, result["compacted"], usage["used"], usage["limit"],
    )
    return result


__all__ = [
    "compact_session",
    "maybe_auto_compact",
    "entries_after_last_compact",
]
