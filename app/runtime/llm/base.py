"""LLM client protocol and shared response types.

Defines the minimal contract every LLM backend (mock, OpenAI-compatible,
future providers) must satisfy: a single ``complete`` coroutine that turns
a list of OpenAI-style chat messages (plus optional tool schemas) into an
:class:`LLMResponse`.

This is intentionally **completion-first** for the foundation thin
vertical — the agent loop awaits the full response and maps it to SSE
events. True token streaming is out of scope here.
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
    """Result of one ``complete`` call.

    ``content`` is the model's textual answer (``None`` when the model
    only emitted tool calls). ``tool_calls`` is empty for plain-text turns.
    """

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        """True when the model requested at least one tool invocation."""
        return bool(self.tool_calls)


@runtime_checkable
class LLMClient(Protocol):
    """Async LLM client contract (completion-first)."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse: ...


__all__ = ["ToolCall", "LLMResponse", "LLMClient"]
