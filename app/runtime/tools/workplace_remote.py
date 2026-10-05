"""Route agent tools to tunnel (WebSocket) or SSH (Paramiko) workplaces.

Supports multi-workplace agents (list / all tunnels / all) and per-turn
overrides via :mod:`app.runtime.tools.workplace_ctx`.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from app.runtime.tools import progress
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
    from app.runtime.access import current_execution, AccessDenied, AccessUnavailable
    from app.services import store
    context = store.access.revalidate(current_execution())
    if agent_id and agent_id != context.agent_id:
        context = store.access.resolve_context(context.user_id, context.session_id, agent_id, parent=context)
    resource_ids = {r.workplace_id for r in context.resources}
    visible = [w for w in store.access.list_visible_workplaces(context.user_id) if w["id"] in resource_ids]
    hint = current_workplace_hint()
    override = current_workplace_id()
    chosen = None
    if hint:
        hit = match_workplace(visible, hint)
        if not hit:
            raise AccessDenied("Requested working location is unavailable")
        chosen = hit["id"]
    elif override:
        if override not in resource_ids:
            raise AccessDenied("Requested working location is unavailable")
        chosen = override
    else:
        chosen = context.active_workplace_id
    if chosen:
        store.access.authorize_resource(context, chosen)
        if context.execution_mode == "unrestricted" and chosen != context.destination_id:
            raise AccessDenied("Unrestricted execution is not activated for this destination")
        wp = store.get_workplace(chosen)
        if not wp:
            raise AccessUnavailable("Execution destination is unavailable")
        kind = (wp.get("kind") or "").strip().lower()
        if kind in {"ssh", "tunnel"}:
            if not wp.get("online"):
                raise AccessUnavailable("Execution destination is offline")
            _require_remote_capability(wp, context.execution_mode)
        return wp
    if not context.legacy_admin:
        raise AccessDenied("An active working location is required")
    # Only the generation-zero bootstrap migration may use the local sentinel.
    # Unknown explicit hints still reject, never fall through locally.
    if hint or override:
        raise AccessDenied("Requested working location is unavailable")
    # The legacy exception authorizes ONLY its migrated local host sentinel;
    # it must not reuse an agent's independent remote bindings as authority.
    return None


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

    if method == "list_dir" and isinstance(result, dict):
        entries = result.get("entries") or []
        if not entries:
            return "(empty directory)"
        lines = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            kind = str(item.get("type") or "file")
            suffix = "/" if kind == "dir" else ("@" if kind == "link" else "")
            lines.append(f"  {name}{suffix}")
            if len(lines) >= 200:
                break
        if result.get("capped"):
            lines.append("  (capped)")
        return "\n".join(lines)

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


def _require_remote_capability(wp: dict[str, Any], mode: str) -> None:
    """Fail closed unless the destination enforces the execution contract.

    Restricted destinations must attest an equivalent per-chat container
    boundary; unrestricted destinations (matching Admin grant + explicit chat
    acknowledgement, checked by the caller) must at least enforce the
    owner/resource/generation envelope. SSH reaches a general-purpose host
    account, so it is unrestricted-only: no destination-side restricted
    boundary can be verified there.
    """
    from app.workplaces import remote_contract
    kind = (wp.get("kind") or "").strip().lower()
    if kind == "ssh":
        # Restricted SSH runs only through the operator-provisioned
        # destination agent (ssh_contract), never a raw cwd jail: the
        # agent enforces the same owner/generation/scope/quota/teardown
        # envelope as tunnels. Unprepared destinations stay denied here;
        # offline/unprepared transports fail closed at call time.
        from app.workplaces import ssh_contract
        ssh_contract.require_ssh_destination(wp, mode=mode)
        return
    remote_contract.require_tunnel_destination(wp, mode=mode)


# Chunked transfer I/O may target enabled transfer-only endpoints on other
# machines. Every other remote method executes only at the active destination.
_TRANSFER_IO_METHODS = frozenset({"read_file_b64", "write_file_b64"})


def _authorize_remote(wp: dict[str, Any], *, transfer_io: bool = False):
    from app.runtime.access import current_execution, AccessDenied
    from app.services import store
    context = store.access.revalidate(current_execution())
    wid = str(wp.get("id") or "")
    resource = store.access.authorize_resource(context, wid)
    if wid == context.destination_id:
        pass
    elif transfer_io and resource.transfer_only:
        # Chunk I/O for an explicit authorized transfer may target an
        # enabled transfer-only endpoint on another machine. Execution
        # methods (exec/process) never take this path: they stay bound to
        # the active destination.
        pass
    else:
        raise AccessDenied("Remote destination is outside activated execution scope")
    if context.execution_mode == "unrestricted" and wid != context.destination_id:
        if not (transfer_io and resource.transfer_only):
            raise AccessDenied("Unrestricted execution is not activated for this destination")
    _require_remote_capability(wp, context.execution_mode)
    return context


def _call_tunnel(
    wp: dict[str, Any],
    method: str,
    params: dict[str, Any],
    timeout: float,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    from app.workplaces import remote_contract
    context = _authorize_remote(wp, transfer_io=method in _TRANSFER_IO_METHODS)
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
    try:
        remote_contract.ensure_admitted(wp, context)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    envelope = remote_contract.build_envelope(context, wid)
    token = None
    wire = dict(params)
    wire["exec_context"] = envelope
    if method in ("exec_bash", "bash"):
        from app.runtime.artifacts.fs import current_session_id
        from app.runtime.tools.user_ctx import current_user_id
        from app.services import secret_store, secret_tunnel

        sid = current_session_id()
        if sid and secret_tunnel.available(wid):
            try:
                token = secret_store.issue_capability(
                    sid, current_user_id(), ttl=timeout + 5, workplace_id=wid
                )
                wire["broker_token"] = token
            except ValueError:
                pass
    try:
        return hub.call(wid, method, wire, timeout=timeout, on_progress=on_progress)
    finally:
        if token:
            secret_store.revoke_capability(token)


def _call_ssh(
    wp: dict[str, Any], method: str, params: dict[str, Any]
) -> dict[str, Any]:
    context = _authorize_remote(wp, transfer_io=method in _TRANSFER_IO_METHODS)
    wid = wp.get("id")
    try:
        from app.services import store
        from app.workplaces import ssh_contract, ssh_exec

        secrets = store.get_workplace_secrets(str(wid)) if wid else None
        if not secrets:
            return {"ok": False, "error": "SSH workplace secrets not found"}
        if context.execution_mode == "restricted":
            # Supervised agent path only: the destination agent enforces
            # the envelope (owner/generation/scopes/RO/deadlines). No raw
            # cwd-jail fallback exists on this branch.
            try:
                transport = ssh_contract.SSHTransport(
                    secrets,
                    root=str(wp.get("ssh_sandbox_root") or ""),
                    workplace_id=str(wid or ""),
                )
            except ssh_contract.AgentTransportError as exc:
                return {"ok": False, "error": str(exc)}
            return ssh_contract.call_via_agent(wp, context, method, params or {}, transport)
        # Unrestricted (checked in _authorize_remote): the matching Admin
        # grant + explicit chat acknowledgement is the authority here,
        # exactly as for unrestricted local execution. OS-account caveats
        # apply and are surfaced wherever the mode is displayed.
        wire = dict(params or {})
        try:
            cap = int(context.quota.duration_seconds or 0)
        except (TypeError, ValueError):
            cap = 0
        if cap > 0:
            try:
                want = float(wire.get("timeout", cap))
            except (TypeError, ValueError):
                want = float(cap)
            wire["timeout"] = min(max(want, 1.0), float(cap))
        return ssh_exec.call(secrets, method, wire)
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
            from app.runtime.access import AccessUnavailable
            raise AccessUnavailable("Execution destination backend is unavailable")
        to = _timeout_seconds(timeout if timeout is not None else params.get("timeout"))
        if kind == "tunnel":
            # Live terminal output for the bash tool card (connector exec-stream).
            sink = progress.current() if method in ("exec_bash", "bash") else None
            payload = _call_tunnel(wp, method, params, to, on_progress=sink)
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
    return try_remote(method, params, timeout=timeout, workplace_hint=workplace_hint)


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
