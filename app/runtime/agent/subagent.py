"""Subagent delegation — run a target agent as a synchronous subagent call.

When the parent agent calls the ``delegate`` tool, the target agent runs a
nested turn, its final output is captured and returned as the delegate tool
result, and the **parent loop continues** with that output fed back — it does
not stop after delegating.

Key mechanisms:

* **Depth tracking** via a :class:`~contextvars.ContextVar` — prevents
  infinite delegation chains (A→B→A→…). Capped at :data:`MAX_DELEGATE_DEPTH`.
* **Result capture** — :func:`drain_subagent_turn` runs a nested
  :func:`~app.runtime.agent.loop.run_turn`, re-emits its events (tagged with
  the target ``agent_id`` so SSE attributes correctly), and returns the
  child's final answer string.
* **Self-delegation guard** — an agent never delegates to itself.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any, AsyncIterator

from app.runtime.llm.base import LLMClient

_logger = logging.getLogger(__name__)

MAX_DELEGATE_DEPTH = 3
MAX_TOOL_RESULT_CHARS = 4000

_depth: ContextVar[int] = ContextVar("delegate_depth", default=0)


def current_depth() -> int:
    """Current delegation nesting depth (0 = top-level turn)."""
    return _depth.get()


def bind_depth(depth: int) -> Token:
    """Set the delegation depth for a nested subagent turn."""
    return _depth.set(depth)


def reset_depth(token: Token | None = None) -> None:
    """Reset delegation depth (tolerates cross-context resets)."""
    if token is not None:
        try:
            _depth.reset(token)
        except ValueError:
            _depth.set(0)
    else:
        _depth.set(0)


def depth_exceeded() -> bool:
    """True when further delegation would exceed the depth cap."""
    return _depth.get() >= MAX_DELEGATE_DEPTH


def _truncate(text: str, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    raw = text if isinstance(text, str) else str(text or "")
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"\n…[truncated, {len(raw)} chars]"


def _subagent_prompt(*, from_id: str, reason: str, user_request: str) -> str:
    """Task brief so the subagent executes directly instead of re-delegating."""
    parts = [
        f"You received a handoff from {from_id}.",
        f"Task: {reason.strip() or 'Handle the user request.'}",
        "Do the work yourself now (run tools as needed). Do not delegate again "
        "unless you truly cannot complete it.",
    ]
    if user_request.strip():
        parts.append(f"User request:\n{user_request}")
    return "\n\n".join(parts)


async def drain_subagent_turn(
    target_agent_id: str,
    *,
    from_agent_id: str,
    reason: str,
    user_request: str,
    history: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    run_turn_fn=None,
    llm: LLMClient | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> AsyncIterator[tuple[dict[str, Any], str]]:
    """Run a nested subagent turn, yielding ``(event, final_output)`` pairs.

    The subagent's events are re-emitted tagged with ``agent_id=target`` so
    the SSE layer attributes them to the right agent. Each yielded value
    carries the running ``final_output`` string (empty until the child emits
    a ``final`` or ``error`` event). The *last* pair carries the complete
    child output.

    ``session_id`` is the parent chat session so ``/auto`` / ``/smart`` /
    ``/manual`` and session allowlists apply to the child. Nested turns also
    skip tool-approval HITL (see :func:`app.runtime.agent.loop._authorize_tool`).

    ``llm`` / ``tools`` default to ``None`` — the subagent resolves its own
    per-agent model and tool set from the store (production). Tests may
    inject them directly. ``run_turn_fn`` defaults to
    :func:`app.runtime.agent.loop.run_turn` (lazy import to avoid a circular
    dependency).
    """
    if run_turn_fn is None:
        from app.runtime.agent.loop import run_turn as run_turn_fn

    token = bind_depth(current_depth() + 1)
    depth = current_depth()
    task_prompt = _subagent_prompt(
        from_id=from_agent_id, reason=reason, user_request=user_request
    )
    _logger.info(
        "subagent begin: target=%s from=%s depth=%d/%d session=%s reason=%s",
        target_agent_id, from_agent_id, depth, MAX_DELEGATE_DEPTH,
        session_id or "-", (reason or "")[:80],
    )
    final_output = ""
    try:
        async for ev in run_turn_fn(
            task_prompt,
            history=history,
            agent_id=target_agent_id,
            session_id=session_id,
            llm=llm,
            tools=tools,
        ):
            # Tag with the subagent's id for SSE attribution.
            ev = dict(ev)
            ev.setdefault("agent_id", target_agent_id)
            ev.setdefault("from_agent_id", from_agent_id)
            ev["subagent"] = True
            if ev["kind"] == "final":
                final_output = ev.get("content") or ""
                _logger.info(
                    "subagent final: target=%s chars=%d",
                    target_agent_id, len(final_output),
                )
                ev["kind"] = "subagent_final"
            elif ev["kind"] == "error":
                final_output = f"Error: {ev.get('message', 'subagent failed')}"
                _logger.warning(
                    "subagent error: target=%s msg=%s",
                    target_agent_id, ev.get("message", "")[:120],
                )
                ev["kind"] = "subagent_error"
            yield ev, final_output
    finally:
        reset_depth(token)
        _logger.info(
            "subagent end: target=%s output=%d chars depth=%d",
            target_agent_id, len(final_output), depth,
        )


__all__ = [
    "MAX_DELEGATE_DEPTH",
    "MAX_TOOL_RESULT_CHARS",
    "current_depth",
    "bind_depth",
    "reset_depth",
    "depth_exceeded",
    "drain_subagent_turn",
]
