"""Route agent tools to tunnel (WebSocket) or SSH (Paramiko) workplaces.

Local workplaces return ``None`` so callers run sandbox logic.
"""

from __future__ import annotations

import json
from typing import Any

from app.runtime.tools.sandbox import current_agent_id
from app.workplaces.hub import hub

_DEFAULT_TIMEOUT = 60.0
_MAX_TIMEOUT = 600.0


def _timeout_seconds(raw: Any, default: float = _DEFAULT_TIMEOUT) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, _MAX_TIMEOUT)


def resolve_agent_workplace(agent_id: str | None = None) -> dict[str, Any] | None:
    aid = agent_id if agent_id is not None else current_agent_id()
    if not aid:
        return None
    try:
        from app.services import store

        return store.resolve_agent_workplace(aid)
    except Exception:
        return None


def agent_remote_kind(agent_id: str | None = None) -> str | None:
    """Return ``tunnel`` / ``ssh`` if agent is on a remote workplace, else ``None``."""
    wp = resolve_agent_workplace(agent_id)
    if not wp:
        return None
    kind = (wp.get("kind") or "").strip().lower()
    if kind in {"tunnel", "ssh"}:
        return kind
    return None


def format_rpc_result(method: str, result: Any) -> str:
    """Turn a remote result into an agent-facing string."""
    if result is None:
        return "(no output)"

    if method in ("exec_bash", "bash", "exec_python") and isinstance(result, dict):
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        try:
            code = int(result.get("exit_code", 0))
        except (TypeError, ValueError):
            code = 0
        parts: list[str] = []
        if stdout:
            parts.append(stdout.rstrip("\n"))
        if stderr:
            parts.append(f"stderr:\n{stderr.rstrip(chr(10))}")
        if code != 0:
            parts.append(f"exit code: {code}")
        if not parts:
            return "(no output)"
        return "\n".join(parts)

    if method == "read_file" and isinstance(result, dict):
        if "content" in result:
            return str(result.get("content") or "")
        if result.get("error"):
            return f"Error: {result['error']}"

    if method in ("write_file", "str_replace", "delete_file") and isinstance(
        result, dict
    ):
        if result.get("ok"):
            path = result.get("path") or ""
            if method == "delete_file":
                return f"Deleted {path}" if path else "Deleted file"
            if method == "str_replace":
                return f"Replaced 1 occurrence in {path}" if path else "Replaced 1 occurrence"
            return f"Wrote file to {path}" if path else "Wrote file"
        if result.get("error"):
            return f"Error: {result['error']}"

    if method == "search_files" and isinstance(result, dict):
        matches = result.get("matches") or []
        if not matches:
            return "No matches"
        header = f"{len(matches)} match(es)"
        if result.get("capped"):
            header += " (capped)"
        return header + "\n" + "\n".join(str(m) for m in matches)

    if method == "process_start" and isinstance(result, dict):
        jid = result.get("id") or "?"
        return f"Started background job {jid}"

    if method in ("process_status", "process_kill") and isinstance(result, dict):
        parts = [
            f"id: {result.get('id')}",
            f"status: {result.get('status')}",
            f"returncode: {result.get('returncode')}",
            f"command: {result.get('command')}",
        ]
        if result.get("stdout"):
            parts.append(f"stdout:\n{str(result['stdout']).rstrip()}")
        if result.get("stderr"):
            parts.append(f"stderr:\n{str(result['stderr']).rstrip()}")
        return "\n".join(parts)

    if method == "process_list":
        if isinstance(result, list):
            if not result:
                return "No background jobs"
            lines = []
            for job in result:
                if not isinstance(job, dict):
                    continue
                lines.append(
                    f"{job.get('id')}: {job.get('status')} "
                    f"rc={job.get('returncode')} cmd={job.get('command')!r}"
                )
            return "\n".join(lines) if lines else "No background jobs"

    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(result)
    return str(result)


def _call_tunnel(method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    wid = None
    wp = resolve_agent_workplace()
    if wp:
        wid = wp.get("id")
    if not wid:
        return {"ok": False, "error": "not a tunnel workplace"}
    if not hub.is_online(str(wid)):
        return {
            "ok": False,
            "error": "tunnel workplace is offline (connector not connected)",
        }
    return hub.call(str(wid), method, dict(params), timeout=timeout)


def _call_ssh(method: str, params: dict[str, Any]) -> dict[str, Any]:
    wp = resolve_agent_workplace()
    if not wp:
        return {"ok": False, "error": "not an ssh workplace"}
    wid = wp.get("id")
    try:
        from app.services import store
        from app.workplaces import ssh_exec

        secrets = store.get_workplace_secrets(str(wid)) if wid else None
        if not secrets:
            return {"ok": False, "error": "SSH workplace secrets not found"}
        return ssh_exec.call(secrets, method, params)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def try_remote(
    method: str,
    params: dict[str, Any],
    *,
    timeout: float | None = None,
) -> str | None:
    """If agent is on tunnel/ssh, run remote method; else ``None`` (use local).

    Offline / failures return ``Error: ...`` strings (never raises).
    """
    kind = agent_remote_kind()
    if kind is None:
        return None
    to = _timeout_seconds(timeout if timeout is not None else params.get("timeout"))
    if kind == "tunnel":
        payload = _call_tunnel(method, params, to)
    elif kind == "ssh":
        payload = _call_ssh(method, params)
    else:
        return None
    if not payload.get("ok"):
        err = payload.get("error") or "remote call failed"
        return f"Error: {err}"
    return format_rpc_result(method, payload.get("result"))


# Back-compat aliases used by existing tools.
def try_tunnel_rpc(
    method: str,
    params: dict[str, Any],
    *,
    timeout: float | None = None,
) -> str | None:
    return try_remote(method, params, timeout=timeout)


def agent_tunnel_workplace_id(agent_id: str | None = None) -> str | None:
    wp = resolve_agent_workplace(agent_id)
    if not wp or (wp.get("kind") or "").lower() != "tunnel":
        return None
    return (wp.get("id") or "").strip() or None


__all__ = [
    "agent_remote_kind",
    "agent_tunnel_workplace_id",
    "format_rpc_result",
    "resolve_agent_workplace",
    "try_remote",
    "try_tunnel_rpc",
]
