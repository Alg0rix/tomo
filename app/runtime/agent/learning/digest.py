"""Turn digests for the learning reviewer.

Compact structured digest: enough signal for durable distill without
replaying the full transcript into the review client.
"""

from __future__ import annotations

from typing import Any

_MAX_TRAIL_CHARS = 8_000
_MAX_USER_EXCERPTS = 3
_MAX_USER_CHARS = 400
_MAX_FINAL_CHARS = 1_800


def compact_tool_trail(
    messages: list[dict[str, Any]], *, limit: int = _MAX_TRAIL_CHARS
) -> str:
    """Build a compact tool trail from OpenAI-style messages."""
    lines: list[str] = []
    errors = 0
    calls = 0
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function")
                if not isinstance(fn, dict):
                    fn = {}
                name = fn.get("name") or "?"
                args = fn.get("arguments") or ""
                if not isinstance(args, str):
                    args = str(args)
                if len(args) > 200:
                    args = args[:200] + "…"
                lines.append(f"→ {name}({args})")
                calls += 1
        elif role == "tool":
            name = msg.get("name") or "tool"
            content = msg.get("content") or ""
            if not isinstance(content, str):
                content = str(content)
            err = content.lstrip().startswith("Error") or "BLOCKED" in content[:40]
            if err:
                errors += 1
            snippet = content.strip().splitlines()[0][:180] if content.strip() else ""
            mark = "✗" if err else "✓"
            lines.append(f"  {mark} {name}: {snippet}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 24] + "\n…(truncated)"
    header = f"[tools={calls} errors={errors}]"
    if not lines:
        return f"{header}\n(no tools)"
    return f"{header}\n{text}"


def _user_excerpts(messages: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        text = content.strip()
        if not text:
            continue
        # Skip synthetic system-ish injections
        if text.startswith("[") and "Active task list" in text[:80]:
            continue
        out.append(text[:_MAX_USER_CHARS])
    return out[-_MAX_USER_EXCERPTS:]


def build_review_digest(
    *,
    messages: list[dict[str, Any]],
    user_message: str | None,
    final_content: str,
    skills_touched: list[str],
    tool_calls: int = 0,
    plan_reason: str = "",
) -> str:
    """Structured digest the reviewer consumes as its sole user message body."""
    trail = compact_tool_trail(messages)
    excerpts = _user_excerpts(messages)
    if user_message and user_message.strip():
        goal = user_message.strip()
    elif excerpts:
        goal = excerpts[-1]
    else:
        goal = "(empty)"

    parts = [
        f"## Trigger\n{plan_reason or 'scheduled_review'}",
        f"## Goal (this turn)\n{goal[:800]}",
    ]
    if len(excerpts) > 1:
        parts.append(
            "## Recent user messages\n"
            + "\n---\n".join(f"- {e}" for e in excerpts[:-1])
        )
    parts.append(f"## Tool trail\n{trail}")
    parts.append(
        "## Skills touched this turn\n"
        + (", ".join(skills_touched) if skills_touched else "(none)")
    )
    final = (final_content or "").strip()[:_MAX_FINAL_CHARS] or "(empty)"
    parts.append(f"## Final answer (excerpt)\n{final}")
    parts.append(
        f"## Stats\ntool_calls={tool_calls} messages={len(messages)} "
        f"skills_touched={len(skills_touched)}"
    )
    parts.append(
        "Act with tools if warranted; otherwise reply exactly: Nothing to save."
    )
    return "\n\n".join(parts)


__all__ = [
    "compact_tool_trail",
    "build_review_digest",
]
