"""Deterministic mock LLM client for tests and local development.

Behaviour (mirrors the real two-step calculator flow the agent loop uses):

* A plain user message -> a fixed acknowledgement string, no tool calls.
* A user message whose content contains ``calculate`` or ``=`` triggers a
  single ``calculator`` tool call on the first ``complete``.
* Once the conversation includes a ``tool`` role message (a tool result),
  the next ``complete`` returns a final text answer instead of another
  tool call.

No network access; the logic is synchronous and wrapped in an ``async``
method only to satisfy the :class:`LLMClient` protocol.
"""

from __future__ import annotations

import re
from typing import Any

from app.runtime.llm.base import LLMResponse, ToolCall

_DEFAULT_REPLY = (
    "I'm a mock LLM. I don't have real knowledge, but I'm ready to help."
)
_CALC_FINAL = "The calculation is complete."  # returned after a tool result
_TOOL_CALL_ID = "call_mock_calculator"

# Substrings that look like arithmetic: digits, operators, parens, dots, spaces.
_ARITH_RE = re.compile(r"[-+/*().\d\s]+")


def _extract_expression(content: str) -> str:
    """Pull an arithmetic expression out of a user prompt.

    Handles ``calculate 2 + 2`` and ``what is 2 + 2 =`` styles. Falls back
    to the trimmed content so the calculator always receives something.
    """
    text = (content or "").strip()
    if not text:
        return "0"
    # Prefer the text that follows the keyword "calculate".
    if "calculate" in text.lower():
        after = text.lower().split("calculate", 1)[1].strip(": ").strip()
        text = after or text
    # An "=" sign means "evaluate the left-hand side".
    if "=" in text:
        left = text.split("=", 1)[0].strip()
        text = left or text
    # Pick the longest arithmetic-looking substring available.
    candidates = [m.strip() for m in _ARITH_RE.findall(text) if m.strip()]
    if candidates:
        return max(candidates, key=len)
    return text or "0"


def _last_user_content(messages: list[dict[str, Any]]) -> str | None:
    """Return the content of the most recent user message, if any."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            return content if isinstance(content, str) else None
    return None


def _is_calc_request(content: str | None) -> bool:
    """True when the prompt should trigger the calculator tool."""
    if not content:
        return False
    return "calculate" in content.lower() or "=" in content


def _has_tool_result(messages: list[dict[str, Any]]) -> bool:
    """True when the conversation already contains a tool result message."""
    return any(msg.get("role") == "tool" for msg in messages)


class MockLLMClient:
    """Deterministic mock implementing the :class:`LLMClient` protocol."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        # Second step of the calculator flow: a tool result is present, so
        # produce the final text answer instead of another tool call.
        if _has_tool_result(messages):
            return LLMResponse(content=_CALC_FINAL, tool_calls=[])

        content = _last_user_content(messages)
        if _is_calc_request(content):
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=_TOOL_CALL_ID,
                        name="calculator",
                        arguments={"expression": _extract_expression(content or "")},
                    )
                ],
            )
        return LLMResponse(content=_DEFAULT_REPLY, tool_calls=[])


__all__ = ["MockLLMClient"]
