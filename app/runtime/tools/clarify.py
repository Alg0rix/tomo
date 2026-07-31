"""clarify tool — schema/backend stub; real wait happens in the agent loop."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    """Fallback if clarify is executed outside the gated loop.

    The agent loop intercepts ``clarify`` and performs HITL. This backend
    only validates arguments for registry tests / direct execute.
    """
    if not isinstance(arguments, dict):
        return "Error: clarify expects a dict of arguments"
    question = arguments.get("question")
    if not isinstance(question, str) or not question.strip():
        return "Error: 'question' argument must be a non-empty string"
    return (
        "Error: clarify must be handled by the agent loop HITL waiter "
        f"(question={question.strip()!r})."
    )


__all__ = ["run"]
