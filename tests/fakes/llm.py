"""Scripted LLM clients for tests — explicit response queues, no heuristics.

Product code uses :class:`~app.runtime.llm.openai_compat.OpenAICompatClient`
only. Tests inject :class:`ScriptedLLM` (or other local stubs) into
``run_turn`` / ``get_llm``.
"""

from __future__ import annotations

from typing import Any

from app.runtime.llm.base import LLMResponse, ToolCall


def text_reply(content: str) -> LLMResponse:
    """Plain-text final response (no tool calls)."""
    return LLMResponse(content=content, tool_calls=[])


def bash_call(command: str, id: str = "call_1") -> LLMResponse:
    """Single ``bash`` tool call."""
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id=id, name="bash", arguments={"command": command})],
    )


def recall_call(query: str, id: str = "call_r") -> LLMResponse:
    """Single ``recall`` tool call."""
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id=id, name="recall", arguments={"query": query})],
    )


def tool_then_text(tool_response: LLMResponse, text: str) -> list[LLMResponse]:
    """One tool-call round followed by a final text reply."""
    return [tool_response, text_reply(text)]


class ScriptedLLM:
    """Pops pre-scripted :class:`LLMResponse` values on each ``complete``.

    Optional ``stream_complete`` yields word deltas for text-only replies,
    then a ``done`` event carrying the same response.
    """

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._queue = list(responses)

    @property
    def remaining(self) -> int:
        return len(self._queue)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        if not self._queue:
            raise AssertionError("ScriptedLLM: no responses left in queue")
        return self._queue.pop(0)

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ):
        """Yield deltas for text-only replies, then ``done`` with the response."""
        resp = await self.complete(messages, tools)
        if resp.content and not resp.has_tool_calls:
            words = resp.content.split(" ")
            for i, w in enumerate(words):
                yield {"type": "delta", "content": w if i == 0 else (" " + w)}
        yield {"type": "done", "response": resp}


__all__ = [
    "ScriptedLLM",
    "text_reply",
    "bash_call",
    "recall_call",
    "tool_then_text",
]
