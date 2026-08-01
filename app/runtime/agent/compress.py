"""Working-memory context compression for long agent turns.

When the assembled message list grows past a soft budget, older tool
exchanges are collapsed into a single summary user message so later LLM
rounds stay within the model window without dropping the system prompt or
the latest user request.
"""
from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)

# Soft budget for the *conversation* portion (system prompt separate).
_DEFAULT_SOFT_LIMIT_TOKENS = 24_000
_KEEP_RECENT_MESSAGES = 12
_SUMMARY_TOOL_EXCERPT = 240


def _estimate_tokens(text: str) -> int:
    """Rough token count (~4 chars per token). Local copy avoids circular imports."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _msg_tokens(msg: dict[str, Any]) -> int:
    parts: list[str] = []
    content = msg.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif content is not None:
        parts.append(str(content))
    if msg.get("tool_calls"):
        parts.append(str(msg["tool_calls"]))
    return _estimate_tokens("\n".join(parts))


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
            if content.startswith("[SYSTEM]"):
                continue
            if content:
                lines.append(f"- user: {content[:200]}")
    return "\n".join(lines)


def maybe_compress_messages(
    messages: list[dict[str, Any]],
    *,
    soft_limit_tokens: int = _DEFAULT_SOFT_LIMIT_TOKENS,
    keep_recent: int = _KEEP_RECENT_MESSAGES,
) -> list[dict[str, Any]]:
    """Return messages, possibly with older mid-conversation turns compressed.

    Preserves the leading system message (if any) and the most recent
    ``keep_recent`` messages. No-op when under the soft token budget.
    """
    if not messages or soft_limit_tokens <= 0:
        return messages

    system: list[dict[str, Any]] = []
    rest = messages
    if messages[0].get("role") == "system":
        system = [messages[0]]
        rest = messages[1:]

    total = sum(_msg_tokens(m) for m in rest)
    if total <= soft_limit_tokens or len(rest) <= keep_recent + 2:
        return messages

    split = max(0, len(rest) - keep_recent)
    # Avoid splitting inside an assistant+tool block: walk back to a user msg.
    while split > 0 and rest[split].get("role") == "tool":
        split -= 1
    while split > 0 and rest[split - 1].get("role") == "assistant":
        # Keep the assistant tool_calls with its tool results in the recent side.
        if rest[split - 1].get("tool_calls"):
            break
        split -= 1

    if split < 2:
        return messages

    prefix = rest[:split]
    suffix = rest[split:]
    summary = {
        "role": "user",
        "content": _summarize_prefix(prefix),
    }
    _logger.info(
        "context compress: dropped=%d kept=%d before_tokens≈%d",
        len(prefix),
        len(suffix),
        total,
    )
    return [*system, summary, *suffix]


__all__ = ["maybe_compress_messages"]
