"""LLM turn loop and tool execution (coordinator only).

Orchestration only — no HTTP, no SSE formatting, no persistence. The loop
calls an :class:`~app.runtime.llm.base.LLMClient` with the OpenAI tool
schemas from the registry, executes any requested tools via the registry,
and yields a stream of internal ``dict`` events for the chat layer (Task 6)
to map onto SSE:

* ``{"kind": "thinking", "content": str}``          # optional reasoning
* ``{"kind": "delta", "content": str}``             # streamed text token/chunk
* ``{"kind": "tool", "tool": str, "args": dict}``
* ``{"kind": "tool_result", "tool": str, "result": str, "error": bool}``
* ``{"kind": "final", "content": str, "already_streamed": bool}``
* ``{"kind": "error", "message": str}``

The loop stops when the model returns plain text (-> ``final``) or when
``max_tool_iterations`` completion rounds are exhausted without a final
answer (-> ``error``). One "iteration" is one LLM completion round (streamed
when the client supports ``stream_complete``), which may carry several tool
calls; every tool call is executed and its result fed back before the next
round.
"""

from __future__ import annotations

import itertools
import json
from typing import Any, AsyncIterator

from app.runtime.agent.context import build_messages, build_system_prompt
from app.runtime.llm import get_llm
from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.tools.registry import execute, get_openai_tools


def _max_tool_iterations() -> int:
    from app.services import store

    try:
        raw = store.get_settings().get("max_tool_iterations", 12)
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 12


async def _llm_round(
    client: LLMClient,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Run one LLM round, preferring token streaming when available.

    Yields ``{"kind": "delta", ...}`` chunks, then
    ``{"kind": "_response", "response": LLMResponse}``.
    """
    stream_fn = getattr(client, "stream_complete", None)
    if stream_fn is None:
        resp = await client.complete(messages, tool_schemas)
        if resp.content and not resp.has_tool_calls:
            yield {"kind": "delta", "content": resp.content}
        yield {"kind": "_response", "response": resp}
        return

    async for ev in stream_fn(messages, tool_schemas):
        if ev.get("type") == "delta" and ev.get("content"):
            yield {"kind": "delta", "content": ev["content"]}
        elif ev.get("type") == "done":
            yield {"kind": "_response", "response": ev["response"]}


async def run_turn(
    user_message: str | None,
    *,
    history: list[dict[str, Any]] | None = None,
    llm: LLMClient | None = None,
    tools: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    max_iterations: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one coordinator turn, yielding internal events.

    ``user_message`` is the new turn input; pass ``None`` when the caller has
    already persisted the user entry into ``history`` (the Task 6 pattern) so
    it is not duplicated. ``llm`` / ``tools`` / ``system_prompt`` /
    ``max_iterations`` default to settings-backed ``get_llm()``,
    ``get_openai_tools()``, ``build_system_prompt(agent_id)``, and
    ``max_tool_iterations`` but are injectable for tests. ``agent_id`` selects
    the per-agent ``SYSTEM.md`` / ``SOUL.md`` from ``$TOMO_HOME`` (via
    :func:`build_system_prompt`); ``session_id`` is a context input for the
    persistence wiring in Task 6. Setup failures (bad LLM config,
    broken tool schema, message assembly) and per-round backend failures are
    surfaced as ``{"kind": "error", ...}`` events — ``run_turn`` never raises
    out to the consumer. The function is an async generator — iterate with
    ``async for``.
    """
    try:
        client = llm if llm is not None else get_llm(agent_id)
        tool_schemas = tools if tools is not None else get_openai_tools()
        limit = (
            max_iterations
            if max_iterations is not None
            else _max_tool_iterations()
        )
        prompt = system_prompt
        if prompt is None:
            prompt = build_system_prompt(agent_id)
        messages = build_messages(history, user_message, system_prompt=prompt)
    except Exception as exc:
        yield {"kind": "error", "message": f"Agent setup failed: {exc}"}
        return

    id_counter = itertools.count()

    iteration = 0
    while iteration < limit:
        iteration += 1
        resp: LLMResponse | None = None
        streamed = False
        try:
            async for piece in _llm_round(client, messages, tool_schemas):
                if piece["kind"] == "delta":
                    streamed = True
                    yield piece
                elif piece["kind"] == "_response":
                    resp = piece["response"]
        except Exception as exc:
            yield {"kind": "error", "message": f"LLM request failed: {exc}"}
            return

        if resp is None:
            yield {"kind": "error", "message": "LLM stream ended without a response"}
            return

        if resp.has_tool_calls and resp.content:
            yield {"kind": "thinking", "content": resp.content}

        if resp.has_tool_calls:
            paired = _with_ids(resp.tool_calls, id_counter)
            messages.append(_assistant_tool_calls_message(resp, paired))
            for cid, call in paired:
                yield {"kind": "tool", "tool": call.name, "args": call.arguments}
                result = execute(call.name, call.arguments)
                error = str(result).startswith("Error:")
                yield {
                    "kind": "tool_result",
                    "tool": call.name,
                    "result": result,
                    "error": error,
                }
                messages.append(
                    {"role": "tool", "tool_call_id": cid, "content": result}
                )
            continue

        yield {
            "kind": "final",
            "content": resp.content or "",
            "already_streamed": streamed,
        }
        return

    yield {
        "kind": "error",
        "message": (
            f"Reached max tool iterations ({limit}) without a final answer."
        ),
    }


def _with_ids(
    tool_calls: list[ToolCall], counter: itertools.count
) -> list[tuple[str, ToolCall]]:
    """Pair each tool call with a non-empty id, synthesising one if missing.

    Some backends (and hand-built test responses) may leave ``id`` empty;
    OpenAI requires every ``tool_calls`` entry and its matching ``tool``
    result to share an id. Synthesised ids draw from ``counter`` — a
    turn-scoped monotonic source shared across completion rounds — so empty
    ids never collide between rounds (``call_0`` from round one is not reused
    in round two).
    """
    out: list[tuple[str, ToolCall]] = []
    for call in tool_calls:
        cid = call.id or f"call_{next(counter)}"
        out.append((cid, call))
    return out


def _assistant_tool_calls_message(
    resp: LLMResponse, paired: list[tuple[str, ToolCall]]
) -> dict[str, Any]:
    """Build the OpenAI assistant message carrying this round's tool calls.

    ``paired`` is the already-id-assigned list from :func:`_with_ids` so the
    ids in the assistant message and the matching ``tool`` results stay in
    lockstep.
    """
    return {
        "role": "assistant",
        "content": resp.content,
        "tool_calls": [
            {
                "id": cid,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for cid, call in paired
        ],
    }


__all__ = ["run_turn"]
