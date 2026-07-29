"""Route agent tools to tunnel (WebSocket) or SSH (Paramiko) workplaces.

Supports multi-workplace agents (list / all tunnels / all) and per-turn
overrides via :mod:`app.runtime.tools.workplace_ctx`.
"""

from __future__ import annotations

import json
from typing import Any

from app.runtime.tools.sandbox import current_agent_id
from app.runtime.tools.workplace_ctx import (
    current_workplace_hint,
    current_workplace_id,
    match_workplace,
)
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


def _agent_allowed_workplaces(agent: dict[str, Any]) -> list[dict[str, Any]]:
    from app.services import store

    all_wps = store.list_workplaces()
    scope = (agent.get("workplace_scope") or "single").strip().lower()
    if scope == "all":
        return list(all_wps)
    if scope == "all_tunnels":
        return [w for w in all_wps if (w.get("kind") or "") == "tunnel"]
    ids = list(agent.get("workplace_ids") or [])
    primary = (agent.get("workplace_id") or "").strip()
    if primary and primary not in ids:
        ids = [primary] + ids
    if not ids:
        return []
    by_id = {w["id"]: w for w in all_wps}
    return [by_id[i] for i in ids if i in by_id]


def resolve_agent_workplace(agent_id: str | None = None) -> dict[str, Any] | None:
    """Pick the workplace for this agent + turn (hint / override / default)."""
    aid = agent_id if agent_id is not None else current_agent_id()
    if not aid:
        return None
    try:
        from app.services import store

        agent = store.get_agent(aid)
    except Exception:
        return None
    if not agent:
        return None

    # Explicit per-turn bind (register_workplace / tool arg / session folder).
    override = current_workplace_id()
    if override:
        try:
            from app.services import store

            wp = store.get_workplace(override)
            if wp:
                return wp
        except Exception:
            pass

    allowed = _agent_allowed_workplaces(agent)
    hint = current_workplace_hint()
    if hint and allowed:
        hit = match_workplace(allowed, hint)
        if hit:
            return hit

    # Chat chose "Tomo work dir": do not auto-bind the agent's permanent local
    # workplace (e.g. main → tmp-work → /tmp). Still allow tunnels/SSH via
    # explicit workplace= or hint.
    try:
        from app.runtime.tools.workplace_ctx import force_work_dir

        if force_work_dir():
            remote_only = [
                w
                for w in allowed
                if (w.get("kind") or "").strip().lower() in ("tunnel", "ssh")
            ]
            if not remote_only:
                return None
            allowed = remote_only
    except Exception:
        pass

    if not allowed:
        # single empty → local sandbox ($TOMO_WORK/<agent>)
        return None

    scope = (agent.get("workplace_scope") or "single").strip().lower()
    # Prefer online tunnel when multiple.
    if scope in ("all_tunnels", "all", "list") and len(allowed) > 1:
        for w in allowed:
            if (w.get("kind") or "") == "tunnel" and hub.is_online(w["id"]):
                return w
        # Named primary if set
        primary = (agent.get("workplace_id") or "").strip()
        for w in allowed:
            if w["id"] == primary:
                return w
        return allowed[0]

    primary = (agent.get("workplace_id") or "").strip()
    if primary:
        for w in allowed:
            if w["id"] == primary:
                return w
    return allowed[0] if allowed else None


def agent_remote_kind(agent_id: str | None = None) -> str | None:
    wp = resolve_agent_workplace(agent_id)
    if not wp:
        return None
    kind = (wp.get("kind") or "").strip().lower()
    if kind in {"tunnel", "ssh"}:
        return kind
    return None


def format_rpc_result(method: str, result: Any) -> str:
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

    if method in ("write_file", "str_replace", "delete_file", "patch") and isinstance(
        result, dict
    ):
        if result.get("ok"):
            path = result.get("path") or ""
            if method == "delete_file":
                return f"Deleted {path}" if path else "Deleted file"
            if method == "str_replace":
                n = result.get("replacements", 1)
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    n = 1
                base = f"Replaced {n} occurrence(s)"
                return f"{base} in {path}" if path else base
            if method == "patch":
                n = result.get("hunks_applied", 0)
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    n = 0
                base = f"Applied {n} hunk(s)"
                return f"{base} to {path}" if path else base
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


def _call_tunnel(
    wp: dict[str, Any], method: str, params: dict[str, Any], timeout: float
) -> dict[str, Any]:
    wid = str(wp.get("id") or "")
    if not wid:
        return {"ok": False, "error": "missing workplace id"}
    if not hub.is_online(wid):
        return {
            "ok": False,
            "error": (
                f"tunnel workplace {wp.get('name') or wid!r} is offline "
                "(connector not connected)"
            ),
        }
    return hub.call(wid, method, dict(params), timeout=timeout)


def _call_ssh(
    wp: dict[str, Any], method: str, params: dict[str, Any]
) -> dict[str, Any]:
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
    workplace_hint: str | None = None,
) -> str | None:
    """If agent has a remote workplace for this turn, run RPC; else ``None``."""
    # Optional per-call hint (e.g. bash workplace=)
    hint_token = None
    if workplace_hint:
        from app.runtime.tools import workplace_ctx as wctx

        hint_token = wctx._workplace_hint.set(workplace_hint)  # noqa: SLF001
    try:
        wp = resolve_agent_workplace()
        if not wp:
            return None
        kind = (wp.get("kind") or "").strip().lower()
        if kind == "local":
            # Local workplace uses path via sandbox, not remote RPC.
            return None
        if kind not in ("tunnel", "ssh"):
            return None
        to = _timeout_seconds(
            timeout if timeout is not None else params.get("timeout")
        )
        if kind == "tunnel":
            payload = _call_tunnel(wp, method, params, to)
        else:
            payload = _call_ssh(wp, method, params)
        if not payload.get("ok"):
            err = payload.get("error") or "remote call failed"
            return f"Error: {err}"
        return format_rpc_result(method, payload.get("result"))
    finally:
        if hint_token is not None:
            try:
                from app.runtime.tools import workplace_ctx as wctx

                wctx._workplace_hint.reset(hint_token)  # noqa: SLF001
            except ValueError:
                pass


def try_tunnel_rpc(
    method: str,
    params: dict[str, Any],
    *,
    timeout: float | None = None,
    workplace_hint: str | None = None,
) -> str | None:
    return try_remote(
        method, params, timeout=timeout, workplace_hint=workplace_hint
    )


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
