"""Active context assembly for inference.

Converts persisted session history entries (the ``ChatEntry`` replay shape
stored by the SQLite ``messages`` table) into the OpenAI-style chat message
list the LLM clients expect, and assembles the full prompt — system +
history + new user message — for a single coordinator turn.

History entry ``type`` values (see the foundation design spec):
``user``, ``final``, ``thinking``, ``tool_call``, ``tool_output``,
``intermediate``, ``error``, ``delegate``. Only the conversational and tool
entries map onto OpenAI roles; ``thinking`` / ``intermediate`` / ``error``
/ ``delegate`` are internal bookkeeping and are skipped so they never leak
into the model context.

This module is pure transformation — no HTTP, no SSE, no persistence. The
``messages`` schema stores no ``tool_call_id``, so consecutive ``tool_call``
entries are grouped into one assistant message and paired with the
immediately following ``tool_output`` entries by order, using synthesised
ids (``hist_call_<n>``). Calls with no matching output get a synthetic
``role: tool`` result so the assistant ``tool_calls`` message is never left
dangling; surplus outputs beyond the number of calls are dropped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core import config

_SYSTEM_PROMPT_PATH = config.REPO_ROOT / "defaults" / "coordinator_system.md"
_FALLBACK_PROMPT = (
    "You are Tomo, a helpful agent. Answer the user clearly and concisely, "
    "and use tools when they help."
)


def coordinator_system_prompt(path: Path | None = None) -> str:
    """Return the coordinator system prompt.

    Reads ``defaults/coordinator_system.md`` when present (falling back to a
    short constant otherwise). ``path`` is injectable so tests can exercise
    the fallback without depending on the repo file.
    """
    target = path if path is not None else _SYSTEM_PROMPT_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return _FALLBACK_PROMPT
    text = text.strip()
    return text or _FALLBACK_PROMPT


def history_to_messages(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Map session history entries to OpenAI-style chat messages.

    ``user`` -> ``{"role": "user", ...}``; ``final`` -> assistant text; a run
    of consecutive ``tool_call`` entries -> one assistant message carrying
    ``tool_calls``; the following run of ``tool_output`` entries -> ``tool``
    role messages paired by position. Calls without a matching output receive
    a synthetic ``"Error: missing tool result"`` tool message; surplus
    outputs beyond the number of calls are dropped. Unknown / internal types
    are skipped.
    """
    messages: list[dict[str, Any]] = []
    if not history:
        return messages

    call_counter = 0
    i = 0
    n = len(history)
    while i < n:
        entry = history[i]
        etype = entry.get("type")

        if etype == "user":
            messages.append({"role": "user", "content": entry.get("content") or ""})
            i += 1
            continue

        if etype == "final":
            messages.append({"role": "assistant", "content": entry.get("content") or ""})
            i += 1
            continue

        if etype == "tool_call":
            calls: list[dict[str, Any]] = []
            while i < n and history[i].get("type") == "tool_call":
                e = history[i]
                cid = f"hist_call_{call_counter}"
                call_counter += 1
                calls.append(
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": e.get("function") or "",
                            "arguments": _dumps_args(e.get("params")),
                        },
                    }
                )
                i += 1
            messages.append({"role": "assistant", "content": None, "tool_calls": calls})
            # Pair the following consecutive tool_output entries by position.
            # Surplus outputs beyond the number of calls are dropped (never
            # mapped onto the last call's id); calls with no matching output
            # get a synthetic tool result so the assistant tool_calls message
            # is never left dangling.
            out_idx = 0
            while i < n and history[i].get("type") == "tool_output":
                e = history[i]
                if out_idx < len(calls):
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": calls[out_idx]["id"],
                            "content": e.get("content") or "",
                        }
                    )
                out_idx += 1
                i += 1
            while out_idx < len(calls):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": calls[out_idx]["id"],
                        "content": "Error: missing tool result",
                    }
                )
                out_idx += 1
            continue

        # thinking / intermediate / error / delegate / unknown -> skip.
        i += 1

    return messages


def build_messages(
    history: list[dict[str, Any]] | None,
    user_message: str | None = None,
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Assemble the full message list for one coordinator turn.

    Layout: ``[system] + history_to_messages(history) + [user]``. The new
    ``user_message`` is appended only when provided — callers that persist
    the user entry into history first may pass ``user_message=None``.
    """
    prompt = system_prompt if system_prompt is not None else coordinator_system_prompt()
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
    messages.extend(history_to_messages(history))
    if user_message:
        messages.append({"role": "user", "content": user_message})
    return messages


def _dumps_args(params: Any) -> str:
    """Serialise tool arguments to the OpenAI ``arguments`` JSON string."""
    if isinstance(params, dict):
        return json.dumps(params)
    if params is None:
        return "{}"
    try:
        return json.dumps(params)
    except (TypeError, ValueError):
        return "{}"


__all__ = [
    "coordinator_system_prompt",
    "history_to_messages",
    "build_messages",
]
