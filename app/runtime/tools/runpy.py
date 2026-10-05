"""Python execution: container for restricted, explicit host/remote otherwise.

A remote transport without a restricted execution capability is rejected before
any RPC. Missing context or backend failure never downgrades to host Python.
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
    from app.runtime.tools.sandbox import dispatch_execution

    dispatched = dispatch_execution("runpy", arguments)
    if dispatched is not None:
        return dispatched
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

    from app.runtime.isolation import host
    from app.runtime.isolation.backend import ContainerBackend
    from app.runtime.tools.sandbox import require_host_execution

    root = resolve_work_root()
    to = min(to, require_host_execution().quota.duration_seconds)
    proc = None
    try:
        proc = host.popen(
            [sys.executable, "-"], cwd=str(root),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        stdout, stderr = ContainerBackend._communicate_bounded(proc, code, to)
        completed = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {to:g}s"
    except OSError as exc:
        return f"Error: could not run python: {exc}"
    finally:
        if proc is not None:
            host.forget(proc)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                pipe.close()

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
