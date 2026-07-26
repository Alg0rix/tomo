"""Bash tool — run a shell command inside the agent work-dir sandbox.

Commands start with ``cwd`` set to ``$TOMO_HOME/agents/<id>/work`` (see
:mod:`app.runtime.tools.sandbox`). A wall-clock timeout caps runaway
processes. Failures and timeouts return ``Error: ...`` strings — never raise.
"""

from __future__ import annotations

import subprocess
from typing import Any

from app.runtime.tools.sandbox import resolve_work_root

_DEFAULT_TIMEOUT = 30.0
_MAX_TIMEOUT = 120.0
_MAX_OUTPUT = 100_000


def _timeout_seconds(raw: Any) -> float:
    if raw is None:
        return _DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT
    if value <= 0:
        return _DEFAULT_TIMEOUT
    return min(value, _MAX_TIMEOUT)


def _clip(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + f"\n...[truncated, {len(text)} chars total]"


def run(arguments: dict[str, Any]) -> str:
    """Execute ``command`` in the sandbox cwd; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: bash expects a dict of arguments"
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return "Error: 'command' argument must be a non-empty string"

    root = resolve_work_root()
    timeout = _timeout_seconds(arguments.get("timeout"))
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout:g}s"
    except OSError as exc:
        return f"Error: could not run command: {exc}"

    stdout = _clip(completed.stdout or "")
    stderr = _clip(completed.stderr or "")
    parts: list[str] = []
    if stdout:
        parts.append(stdout.rstrip("\n"))
    if stderr:
        parts.append(f"stderr:\n{stderr.rstrip(chr(10))}")
    if completed.returncode != 0:
        parts.append(f"exit code: {completed.returncode}")
    if not parts:
        return "(no output)"
    return "\n".join(parts)


__all__ = ["run"]
