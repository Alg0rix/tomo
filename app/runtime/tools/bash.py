"""Bash tool — run a shell command inside the agent work-dir sandbox.

Commands start with ``cwd`` set to ``$TOMO_HOME/agents/<id>/work`` (see
:mod:`app.runtime.tools.sandbox`). A wall-clock timeout caps runaway
processes. Failures and timeouts return ``Error: ...`` strings — never raise.

When ``background`` is true, the command is started without waiting and
registered in :mod:`app.runtime.tools.process_registry`.
"""

from __future__ import annotations

import codecs
import os
import signal
import subprocess
import threading
import time
from typing import Any

from app.runtime.tools import progress
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


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _pump(fd: int, buf: list[str], sink: progress.Sink | None, budget: list[int]) -> None:
    """Read ``fd`` until EOF, keeping text in ``buf`` and forwarding it to ``sink``."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        try:
            data = os.read(fd, 4096)
        except OSError:
            data = b""
        text = decoder.decode(data, final=not data)
        if text:
            buf.append(text)
            if sink is not None and budget[0] > 0:
                piece = text[: budget[0]]
                budget[0] -= len(piece)
                try:
                    sink(piece if budget[0] > 0 else piece + "\n…[live output truncated]\n")
                except Exception:
                    pass
        if not data:
            return


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _run_streaming(command: str, cwd: str, timeout: float) -> tuple[int, str, str]:
    from app.services.secret_store import shell_environment

    with shell_environment(timeout, work_root=cwd) as env:
        return _run_streaming_with_env(command, cwd, timeout, env)


def _run_streaming_with_env(command: str, cwd: str, timeout: float, env: dict[str, str]) -> tuple[int, str, str]:
    """Run ``command`` like ``subprocess.run(capture_output=True)`` but stream live.

    Chunks from stdout and stderr go to the bound :mod:`progress` sink as they
    arrive. Raises :class:`subprocess.TimeoutExpired` after killing the whole
    process group.
    """
    sink = progress.current()
    proc = subprocess.Popen(
        ["bash", "-lc", command],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out: list[str] = []
    err: list[str] = []
    budget = [_MAX_OUTPUT]
    readers = [
        threading.Thread(target=_pump, args=(proc.stdout.fileno(), out, sink, budget), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr.fileno(), err, sink, budget), daemon=True),
    ]
    for t in readers:
        t.start()
    deadline = time.monotonic() + timeout
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait()
        raise
    finally:
        # Background grandchildren may hold the pipes open; don't wait on them forever.
        for t in readers:
            t.join(max(0.5, deadline - time.monotonic()))
        proc.stdout.close()
        proc.stderr.close()
    return proc.returncode, _normalize("".join(out)), _normalize("".join(err))


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
        from app.services.background_jobs import manager

        try:
            job = manager.start(command, workplace_hint=wp_hint)
        except Exception as exc:
            return f"Error: could not start background command: {exc}"
        return (
            f"Started background job {job['id']}\n"
            f"status: {job['status']}\ncommand: {job['command']}\n"
            f"backend: {job['backend']}\nworkplace: {job['workplace_id']}\n"
            "The process continues after this turn; completion returns to this conversation."
        )

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
            returncode, stdout, stderr = _run_streaming(command, str(root), timeout)
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

    stdout = _clip(stdout)
    stderr = _clip(stderr)
    parts: list[str] = []
    if stdout:
        parts.append(stdout.rstrip("\n"))
    if stderr:
        parts.append(f"stderr:\n{stderr.rstrip(chr(10))}")
    if returncode != 0:
        parts.append(f"exit code: {returncode}")
    if not parts:
        return "(no output)"
    return "\n".join(parts)


__all__ = ["run"]
