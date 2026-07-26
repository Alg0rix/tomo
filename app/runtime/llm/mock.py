"""Deterministic mock LLM client for tests and local development.

Behaviour (mirrors the real two-step tool flows the agent loop uses):

* A plain user message -> a fixed acknowledgement string, no tool calls.
* A user message whose content contains ``calculate`` or ``=`` triggers a
  single ``calculator`` tool call on the first ``complete`` — but only when
  ``tools`` advertises a function named ``calculator``.
* A user message that looks like a knowledge question (contains ``recall``,
  ``remember``, ``knowledge``, ``deadline``, ``vendor``, ``what is``, or
  ``what's``) triggers ``recall`` when that tool is advertised — unless a
  calculator request takes priority.
* Once the conversation includes a ``tool`` role message *after the most
  recent user message* (i.e. mid-turn), the next ``complete`` returns a
  final text answer instead of another tool call. Tool results from earlier
  turns do not suppress a fresh tool call triggered by a new user message.

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
_RECALL_FINAL = "I found the relevant knowledge base entry."
_TOOL_CALL_ID = "call_mock_calculator"
_RECALL_CALL_ID = "call_mock_recall"

# Substrings that look like arithmetic: digits, operators, parens, dots, spaces.
_ARITH_RE = re.compile(r"[-+/*().\d\s]+")

_RECALL_HINTS = (
    "recall",
    "remember",
    "knowledge",
    "deadline",
    "vendor",
    "what is",
    "what's",
    "faq",
)


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
    lower = content.lower()
    if "calculate" in lower:
        return True
    # Bare "=" with arithmetic — avoid treating knowledge "what is X" as calc.
    if "=" in content:
        return True
    return False


def _is_recall_request(content: str | None) -> bool:
    """True when the prompt should trigger the recall tool."""
    if not content:
        return False
    lower = content.lower()
    return any(hint in lower for hint in _RECALL_HINTS)


def _extract_recall_query(content: str) -> str:
    """Derive a search query from the user prompt."""
    text = (content or "").strip()
    lower = text.lower()
    if "recall" in lower:
        after = text[lower.index("recall") + len("recall") :].strip(": ").strip()
        if after:
            return after
    return text or "knowledge"


def _has_tool_result_after_last_user(messages: list[dict[str, Any]]) -> bool:
    """True when a tool result message follows the most recent user message.

    Tool results from earlier turns must NOT suppress a fresh tool
    call triggered by a new user message, so only messages after the last
    ``user`` entry are considered. With no user message at all we fall back
    to the legacy "any tool result -> final text" behaviour.
    """
    last_user_idx = None
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "user":
            last_user_idx = idx
            break
    if last_user_idx is None:
        return any(msg.get("role") == "tool" for msg in messages)
    return any(
        messages[idx].get("role") == "tool"
        for idx in range(last_user_idx + 1, len(messages))
    )


def _tool_named(tools: list[dict[str, Any]] | None, name: str) -> bool:
    """True when ``tools`` advertises a function named ``name``."""
    if not tools:
        return False
    for tool in tools:
        fn = (tool or {}).get("function") or {}
        if fn.get("name") == name:
            return True
    return False


def _calculator_available(tools: list[dict[str, Any]] | None) -> bool:
    return _tool_named(tools, "calculator")


def _recall_available(tools: list[dict[str, Any]] | None) -> bool:
    return _tool_named(tools, "recall")


def _last_tool_name(messages: list[dict[str, Any]]) -> str | None:
    """Best-effort: name of the most recent assistant tool call this turn."""
    last_user_idx = None
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "user":
            last_user_idx = idx
            break
    start = 0 if last_user_idx is None else last_user_idx + 1
    for idx in range(len(messages) - 1, start - 1, -1):
        msg = messages[idx]
        if msg.get("role") != "assistant":
            continue
        calls = msg.get("tool_calls") or []
        if calls:
            fn = (calls[0].get("function") or {}) if isinstance(calls[0], dict) else {}
            name = fn.get("name")
            if isinstance(name, str):
                return name
    return None


class MockLLMClient:
    """Deterministic mock implementing the :class:`LLMClient` protocol."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        # Mid-turn: a tool result has come back since the last user message,
        # so produce the final text answer instead of another tool call.
        if _has_tool_result_after_last_user(messages):
            if _last_tool_name(messages) == "recall":
                return LLMResponse(content=_RECALL_FINAL, tool_calls=[])
            return LLMResponse(content=_CALC_FINAL, tool_calls=[])

        content = _last_user_content(messages)
        if _is_calc_request(content) and _calculator_available(tools):
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
        if _is_recall_request(content) and _recall_available(tools):
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=_RECALL_CALL_ID,
                        name="recall",
                        arguments={"query": _extract_recall_query(content or "")},
                    )
                ],
            )
        return LLMResponse(content=_DEFAULT_REPLY, tool_calls=[])

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ):
        """Yield the full reply as one delta (tests), then a done response."""
        resp = await self.complete(messages, tools)
        if resp.content and not resp.has_tool_calls:
            words = resp.content.split(" ")
            for i, w in enumerate(words):
                yield {"type": "delta", "content": w if i == 0 else (" " + w)}
        yield {"type": "done", "response": resp}


__all__ = ["MockLLMClient"]
