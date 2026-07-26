"""runpy tool — execute a Python snippet (local sandbox or tunnel exec_python).

Uses ``exec_python`` on tunnel/SSH workplaces.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from app.runtime.tools.sandbox import resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc

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
    """Run Python ``code``; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: runpy expects a dict of arguments"
    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        return "Error: 'code' argument must be a non-empty string"

    to = _timeout_seconds(arguments.get("timeout"))
    remote = try_tunnel_rpc(
        "exec_python",
        {"code": code, "timeout": int(to), "env": {}, "cwd": ""},
        timeout=to + 10.0,
    )
    if remote is not None:
        return remote

    root = resolve_work_root()
    try:
        completed = subprocess.run(
            [sys.executable, "-"],
            input=code,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=to,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {to:g}s"
    except OSError as exc:
        return f"Error: could not run python: {exc}"

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
