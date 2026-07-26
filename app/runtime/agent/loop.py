"""LLM turn loop and tool execution (coordinator only).

Orchestration only — no HTTP, no SSE formatting, no persistence. The loop
calls an :class:`~app.runtime.llm.base.LLMClient` with the OpenAI tool
schemas from the registry, executes any requested tools via the registry,
and yields a stream of internal ``dict`` events for the chat layer (Task 6)
to map onto SSE:

* ``{"kind": "thinking", "content": str}``          # optional reasoning
* ``{"kind": "tool", "tool": str, "args": dict}``
* ``{"kind": "tool_result", "tool": str, "result": str, "error": bool}``
* ``{"kind": "final", "content": str}``
* ``{"kind": "error", "message": str}``

The loop stops when the model returns plain text (-> ``final``) or when
``LLM_MAX_TOOL_ITERATIONS`` completion rounds are exhausted without a final
answer (-> ``error``). One "iteration" is one ``complete()`` round, which
may carry several tool calls; every tool call is executed and its result
fed back before the next round.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from app.core import config
from app.runtime.agent.context import build_messages
from app.runtime.llm import get_llm
from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.tools.registry import execute, get_openai_tools


async def run_turn(
    user_message: str,
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

    ``llm`` / ``tools`` / ``system_prompt`` / ``max_iterations`` default to
    the app configuration (``get_llm()``, ``get_openai_tools()``,
    ``coordinator_system_prompt()``, ``LLM_MAX_TOOL_ITERATIONS``) but are
    injectable for tests. ``agent_id`` / ``session_id`` are accepted as
    context inputs for the persistence wiring in Task 6 and are not used
    here. The function is an async generator — iterate with ``async for``.
    """
    client = llm if llm is not None else get_llm()
    tool_schemas = tools if tools is not None else get_openai_tools()
    limit = (
        max_iterations if max_iterations is not None else config.LLM_MAX_TOOL_ITERATIONS
    )

    messages = build_messages(history, user_message, system_prompt=system_prompt)

    iteration = 0
    while iteration < limit:
        iteration += 1
        try:
            resp = await client.complete(messages, tool_schemas)
        except Exception as exc:  # surface any backend failure as an error event
            yield {"kind": "error", "message": f"LLM request failed: {exc}"}
            return

        # Optional reasoning emitted alongside tool calls.
        if resp.has_tool_calls and resp.content:
            yield {"kind": "thinking", "content": resp.content}

        if resp.has_tool_calls:
            messages.append(_assistant_tool_calls_message(resp))
            for cid, call in _with_ids(resp.tool_calls):
                yield {"kind": "tool", "tool": call.name, "args": call.arguments}
                result = execute(call.name, call.arguments)
                error = result.startswith("Error")
                yield {
                    "kind": "tool_result",
                    "tool": call.name,
                    "result": result,
                    "error": error,
                }
                messages.append(
                    {"role": "tool", "tool_call_id": cid, "content": result}
                )
            continue  # feed results back and run the next round

        # Plain-text answer -> final.
        yield {"kind": "final", "content": resp.content or ""}
        return

    # Exhausted the iteration budget without a final answer.
    yield {
        "kind": "error",
        "message": (
            f"Reached max tool iterations ({limit}) without a final answer."
        ),
    }


def _with_ids(tool_calls: list[ToolCall]) -> list[tuple[str, ToolCall]]:
    """Pair each tool call with a non-empty id, synthesising one if missing.

    Some backends (and hand-built test responses) may leave ``id`` empty;
    OpenAI requires every ``tool_calls`` entry and its matching ``tool``
    result to share an id, so a stable fallback keeps the message chain
    well-formed.
    """
    out: list[tuple[str, ToolCall]] = []
    for i, call in enumerate(tool_calls):
        cid = call.id or f"call_{i}"
        out.append((cid, call))
    return out


def _assistant_tool_calls_message(resp: LLMResponse) -> dict[str, Any]:
    """Build the OpenAI assistant message carrying this round's tool calls."""
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
            for cid, call in _with_ids(resp.tool_calls)
        ],
    }


__all__ = ["run_turn"]
