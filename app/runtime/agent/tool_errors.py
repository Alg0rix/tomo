"""Shared tool-result failure detection for the agent loop and ATG.

Tomo tools return strings. Hard failures use an ``Error:`` / ``BLOCKED``
prefix; bash-style results may include a trailing ``exit code: N`` line.
"""
from __future__ import annotations

import re
from typing import Any

_EXIT_CODE_RE = re.compile(r"(?m)^exit code:\s*(\d+)\s*$")


def tool_result_is_error(result: Any, *, empty_is_error: bool = False) -> bool:
    """Return True when a tool result string indicates failure.

    ``empty_is_error`` is False by default: quiet successful tools (e.g. bash
    with no stdout and exit 0) must not be treated as failures. ATG previously
    treated empty as failure — that caused false-positive fallbacks.
    """
    text = str(result or "")
    if text.startswith("Error:") or text.startswith("BLOCKED"):
        return True
    m = _EXIT_CODE_RE.search(text)
    if m and int(m.group(1)) != 0:
        return True
    if empty_is_error and not text.strip():
        return True
    return False


__all__ = ["tool_result_is_error"]
