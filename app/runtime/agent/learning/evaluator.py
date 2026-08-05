"""Soft evaluator for learning-review writes.

Gates ``saved=1``: tool must succeed, not be a curated near-duplicate noop,
and belong to a write tool. Provider failures stay out of the ledger
(handled in the runner).
"""

from __future__ import annotations

from typing import Any


def is_error_result(result_text: str) -> bool:
    text = (result_text or "").strip()
    return text.startswith("Error") or text.startswith("Error:")


def is_near_duplicate_noop(result_text: str) -> bool:
    """Curated USER/agent/project adds that skipped a near-duplicate."""
    low = (result_text or "").lower()
    return "near-duplicate" in low or "already present" in low


def evaluate_write(
    tool_name: str,
    result_text: str,
    *,
    write_tools: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return evaluation verdict for one review tool outcome."""
    from app.runtime.agent.learning.memory_types import WRITE_TOOLS

    name = (tool_name or "").strip()
    allowed = write_tools if write_tools is not None else WRITE_TOOLS
    text = (result_text or "").strip()
    if name not in allowed:
        return {
            "ok": False,
            "reason": "not_write_tool",
            "saved_eligible": False,
        }
    if is_error_result(text):
        return {"ok": False, "reason": "error", "saved_eligible": False}
    if is_near_duplicate_noop(text):
        return {
            "ok": False,
            "reason": "near_duplicate",
            "saved_eligible": False,
        }
    return {"ok": True, "reason": "ok", "saved_eligible": True}


__all__ = [
    "is_error_result",
    "is_near_duplicate_noop",
    "evaluate_write",
]
