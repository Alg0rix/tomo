"""LLM client protocol and shared response types.

Defines the minimal contract every LLM backend must satisfy: ``complete``
for a full response, and optionally ``stream_complete`` for token deltas
(OpenAI-compatible streaming). The agent loop prefers ``stream_complete``
when present so the UI can render tokens as they arrive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """A single tool invocation requested by the model.

    ``arguments`` is the already-parsed JSON object (never a raw string)
    so the agent loop can dispatch directly to the tool registry.
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Result of one ``complete`` / stream assembly.

    ``content`` is the model's textual answer (``None`` when the model
    only emitted tool calls). ``tool_calls`` is empty for plain-text turns.

    ``prompt_tokens`` / ``completion_tokens`` come from the provider ``usage``
    object when available (0 when the backend omitted them).
    """

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def has_tool_calls(self) -> bool:
        """True when the model requested at least one tool invocation."""
        return bool(self.tool_calls)


@runtime_checkable
class LLMClient(Protocol):
    """Async LLM client contract."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...


# Optional duck-typed method (not on Protocol):
# async def stream_complete(messages, tools=None) -> AsyncIterator[dict]
#   yields {"type": "delta", "content": str}
#   yields {"type": "done", "response": LLMResponse}


__all__ = ["ToolCall", "LLMResponse", "LLMClient"]
