"""Working-memory context compression for long agent turns.

When the assembled message list grows past a soft budget, older tool
exchanges are collapsed into a single summary user message so later LLM
rounds stay within the model window without dropping the system prompt or
the latest user request.
"""
from __future__ import annotations

import logging
import json
from typing import Any

_logger = logging.getLogger(__name__)

# Legacy callers can supply a fixed budget; runtime uses the active model window.
_DEFAULT_SOFT_LIMIT_TOKENS = 24_000
_KEEP_RECENT_MESSAGES = 12
_SUMMARY_TOOL_EXCERPT = 240


def _estimate_tokens(text: str) -> int:
    """Rough token count (~4 UTF-8 bytes per token), not an exact tokenizer."""
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _msg_tokens(msg: dict[str, Any]) -> int:
    parts: list[str] = []
    image_tokens = 0
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                # Images are tokenized as image patches, not as base64 text.
                # Allow conservative image headroom; provider overflow still
                # goes through the bounded recovery path.
                image_tokens += 16_384
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
    elif content is not None:
        parts.append(str(content))
    if msg.get("tool_calls"):
        parts.append(str(msg["tool_calls"]))
    return _estimate_tokens("\n".join(parts)) + image_tokens + 8


class ContextBudgetError(ValueError):
    """Required instructions/request cannot fit without silently losing input."""


def prompt_budget(context_window: int) -> int:
    """Leave output room plus a margin for estimated token counts/wire framing."""
    output = min(8192, max(1, context_window // 4))
    margin = max(1, context_window // 20)
    return max(0, context_window - output - margin)


def estimate_prompt_tokens(messages: list[dict[str, Any]], tools: list[dict] | None = None) -> int:
    tool_tokens = _estimate_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0
    return sum(_msg_tokens(message) for message in messages) + tool_tokens


def _summarize_prefix(messages: list[dict[str, Any]]) -> str:
    lines = [
        "[SYSTEM] Earlier conversation was compressed to save context. "
        "Key points from prior tool work:"
    ]
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            body = str(msg.get("content") or "")
            excerpt = body[:_SUMMARY_TOOL_EXCERPT]
            if len(body) > _SUMMARY_TOOL_EXCERPT:
                excerpt += "…"
            lines.append(f"- tool: {excerpt}")
        elif role == "assistant":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                lines.append(f"- assistant: {content.strip()[:200]}")
            elif msg.get("tool_calls"):
                names = [
                    (tc.get("function") or {}).get("name") or "?"
                    for tc in msg["tool_calls"]
                    if isinstance(tc, dict)
                ]
                lines.append(f"- assistant called: {', '.join(names)}")
        elif role == "user":
            content = str(msg.get("content") or "").strip()
            if content:
                lines.append(f"- user: {content[:200]}")
    return "\n".join(lines)


def maybe_compress_messages(
    messages: list[dict[str, Any]],
    *,
    soft_limit_tokens: int = _DEFAULT_SOFT_LIMIT_TOKENS,
    keep_recent: int = _KEEP_RECENT_MESSAGES,
    context_window: int | None = None,
    tools: list[dict] | None = None,
    protect_latest_user: bool = True,
) -> list[dict[str, Any]]:
    """Return messages, possibly with older mid-conversation turns compressed.

    Preserve instructions and the latest request. Recent history is preferred,
    but can be summarized to fit. Tool calls/results are kept or removed together.
    Raises ContextBudgetError when required input alone exceeds the budget.
    """
    budget = prompt_budget(context_window) if context_window is not None else soft_limit_tokens
    if (not messages and not tools) or (context_window is None and budget <= 0):
        return messages
    before_tokens = estimate_prompt_tokens(messages, tools)
    if before_tokens <= budget:
        return messages

    # Group contiguous tool results with their assistant call. Keep instruction
    # messages in their original position rather than moving live context earlier.
    groups: list[list[dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "tool" and groups and (
            groups[-1][0].get("tool_calls") or groups[-1][0].get("role") == "tool"
        ):
            groups[-1].append(message)
        else:
            groups.append([message])
    protected = [group for group in groups if group[0].get("role") in {"system", "developer"}]
    latest_user = next((group for group in reversed(groups) if group[0].get("role") == "user"), None)
    if protect_latest_user and latest_user is not None:
        protected.append(latest_user)
    required = [message for group in protected for message in group]
    if estimate_prompt_tokens(required, tools) > budget:
        raise ContextBudgetError(
            "The system instructions, tool definitions and latest request exceed the model's "
            "prompt budget. Shorten the request, reduce tools/instructions, or select a larger-context model."
        )

    dropped: list[dict[str, Any]] = []
    summary_budget = min(2048, max(0, (budget - estimate_prompt_tokens(required, tools)) // 4))
    remaining_tokens = before_tokens
    remaining_messages = len(messages)

    def over_budget() -> bool:
        return remaining_tokens + (summary_budget if dropped else 0) > budget

    def assembled() -> list[dict[str, Any]]:
        result = [message for group in groups for message in group]
        if dropped and summary_budget >= 64:
            text = _summarize_prefix(dropped)
            # Bound the summary itself, including thousands of historical turns.
            while _estimate_tokens(text) + 8 > summary_budget:
                text = text[:max(0, len(text) // 2)]
            summary = {"role": "user", "content": text}
            index = 0
            while index < len(result) and result[index].get("role") in {"system", "developer"}:
                index += 1
            result.insert(index, summary)
        return result

    def drop_oldest() -> bool:
        nonlocal remaining_tokens, remaining_messages
        for index, group in enumerate(groups):
            if any(group is item for item in protected):
                continue
            dropped.extend(groups.pop(index))
            remaining_tokens -= sum(_msg_tokens(message) for message in group)
            remaining_messages -= len(group)
            return True
        return False

    # Prefer the recent tail, but never exempt a short history from its budget.
    while remaining_messages > keep_recent and over_budget():
        if not drop_oldest():
            break

    # Oversized tool results are expendable; IDs and call arguments stay intact.
    while over_budget():
        candidates = [(gi, mi, message) for gi, group in enumerate(groups)
                      for mi, message in enumerate(group) if message.get("role") == "tool"
                      and isinstance(message.get("content"), str) and len(message["content"]) > 256]
        if not candidates:
            break
        gi, mi, message = max(candidates, key=lambda item: len(item[2]["content"]))
        replacement = {**message, "content": message["content"][:len(message["content"]) // 2]
                       + "\n[Tool result truncated to fit context; retrieve specific details if needed.]"}
        remaining_tokens += _msg_tokens(replacement) - _msg_tokens(message)
        groups[gi] = [*groups[gi][:mi], replacement, *groups[gi][mi + 1:]]

    while over_budget() and drop_oldest():
        pass
    result = assembled()
    if estimate_prompt_tokens(result, tools) > budget:
        # Required input fits, but even a summary may exceed the remaining room.
        summary_budget = 0
        result = assembled()
    _logger.info("context compress: dropped=%d before_tokens≈%d after_tokens≈%d budget=%d",
                 len(dropped), before_tokens, estimate_prompt_tokens(result, tools), budget)
    return result


__all__ = ["maybe_compress_messages", "prompt_budget", "estimate_prompt_tokens", "ContextBudgetError"]
