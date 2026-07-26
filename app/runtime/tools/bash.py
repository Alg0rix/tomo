"""Bash tool — run a shell command inside the agent work-dir sandbox.

Commands start with ``cwd`` set to ``$TOMO_HOME/agents/<id>/work`` (see
:mod:`app.runtime.tools.sandbox`). A wall-clock timeout caps runaway
processes. Failures and timeouts return ``Error: ...`` strings — never raise.

When ``background`` is true, the command is started without waiting and
registered in :mod:`app.runtime.tools.process_registry`.
"""

from __future__ import annotations

import subprocess
from typing import Any

from app.runtime.tools import process_registry
from app.runtime.tools.sandbox import current_agent_id, resolve_work_root
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


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return False


def run(arguments: dict[str, Any]) -> str:
    """Execute ``command`` in the sandbox cwd; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: bash expects a dict of arguments"
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return "Error: 'command' argument must be a non-empty string"

    wp_hint = arguments.get("workplace") or arguments.get("workplace_id")
    if isinstance(wp_hint, str):
        wp_hint = wp_hint.strip() or None
    else:
        wp_hint = None

    background = _truthy(arguments.get("background"))
    if background:
        # Tunnel / SSH: start remote background job via process_start.
        remote_bg = try_tunnel_rpc(
            "process_start",
            {"command": command, "cwd": ""},
            timeout=30.0,
            workplace_hint=wp_hint,
        )
        if remote_bg is not None:
            return remote_bg
        root = resolve_work_root()
        try:
            proc = subprocess.Popen(
                ["bash", "-lc", command],
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            return f"Error: could not start background command: {exc}"
        job = process_registry.register(
            command, proc, agent_id=current_agent_id()
        )
        return f"Started background job {job.id}"

    # Remote exec_bash on tunnel / SSH workplaces.
    to = _timeout_seconds(arguments.get("timeout"))
    remote = try_tunnel_rpc(
        "exec_bash",
        {
            "script": command,
            "timeout": int(to),
            "env": {},
            "cwd": "",
        },
        timeout=to + 10.0,
        workplace_hint=wp_hint,
    )
    if remote is not None:
        return remote

    # Local sandbox: optionally bind workplace hint so multi-wp agents hit
    # the right root_path, then capture the resolved path before unbinding.
    root_tokens = None
    if wp_hint:
        try:
            from app.runtime.tools.workplace_ctx import bind_workplace

            root_tokens = bind_workplace(hint=wp_hint)
        except Exception:
            root_tokens = None
    try:
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
    finally:
        if root_tokens is not None:
            try:
                from app.runtime.tools.workplace_ctx import reset_workplace

                reset_workplace(root_tokens)
            except Exception:
                pass

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
