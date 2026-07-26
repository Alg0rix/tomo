"""clarify tool — ask the user a clarifying question."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    """Return ``CLARIFY: {question}`` for the channel layer to surface."""
    if not isinstance(arguments, dict):
        return "Error: clarify expects a dict of arguments"
    question = arguments.get("question")
    if not isinstance(question, str) or not question.strip():
        return "Error: 'question' argument must be a non-empty string"
    return f"CLARIFY: {question.strip()}"


__all__ = ["run"]
