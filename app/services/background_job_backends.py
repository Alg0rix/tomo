"""Structured background-job RPCs pinned to the workplace captured at start."""

from __future__ import annotations

from typing import Any

from app.workplaces.remote_contract import REMOTE_EXEC_CONTRACT


class RemoteStartRejected(RuntimeError):
    """Backend confirmed that execution was rejected before creating a job."""


class RemoteStartUnknown(RuntimeError):
    """Start may have executed; retain the correlation handle, never retry it."""

    def __init__(self, message: str, handle: str) -> None:
        super().__init__(message)
        self.handle = self.backend_handle = handle


def resolve_backend(workplace_hint: str | None = None) -> tuple[str, str, str]:
    from app.runtime.access import AccessDenied, AccessUnavailable, current_execution
    from app.runtime.isolation.backend import backend as container_backend

    context = container_backend.access.require_tool(current_execution(), "bash")
    if workplace_hint and workplace_hint != context.active_workplace_id:
        raise AccessDenied("Background workplace override is outside the selected destination")
    if context.execution_mode == "restricted":
        active = next(r for r in context.resources if r.workplace_id == context.active_workplace_id)
        if active.transfer_only:
            raise AccessDenied("Background execution requires the active destination, not a transfer endpoint")
        if active.kind == "tunnel":
            from app.services import store
            from app.workplaces import remote_contract
            wp = store.get_workplace(active.workplace_id)
            remote_contract.require_tunnel_destination(wp or {"id": active.workplace_id}, mode="restricted")
            # Empty cwd: the destination resolves its active scope root
            # (the coordinator never invents destination paths).
            return "tunnel", active.workplace_id, ""
        container_backend._destination(context)
        return "container", active.workplace_id, active.mount_path
    if context.resources:
        active = next(r for r in context.resources if r.workplace_id == context.active_workplace_id)
        if active.transfer_only:
            raise AccessDenied("Background execution requires the active destination, not a transfer endpoint")
        if active.kind == "tunnel":
            # Unrestricted tunnel background work runs under the destination
            # OS account (matching grant + explicit acknowledgement, already
            # enforced by the context); the envelope below binds owner,
            # session, generation and duration, and the destination
            # supervises the process tree. SSH has no destination-side
            # enforcement, so only its synchronous exec path is offered.
            from app.services import store
            from app.workplaces import remote_contract
            wp = store.get_workplace(active.workplace_id)
            remote_contract.require_tunnel_destination(wp or {"id": active.workplace_id}, mode="unrestricted")
            # Empty cwd: the destination resolves its active scope root
            # (the coordinator never invents destination paths).
            return "tunnel", active.workplace_id, ""
        if active.kind != "local":
            raise AccessUnavailable("Remote background destination has no verified owned, duration-bounded supervised boundary")
        return active.kind, active.workplace_id, active.root_path

    # The only resource-less exception is the foundation's migrated bootstrap
    # LOCAL sentinel. Never reinterpret an agent/hint against a global catalog.
    if not context.legacy_admin or context.destination_id != "__legacy_host__":
        raise AccessDenied("An explicit background execution destination is required")
    from app.runtime.tools.sandbox import resolve_work_root

    return "local", "", str(resolve_work_root())


def _rpc(backend: str, workplace_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.access import current_execution
    from app.services import store
    from app.workplaces import remote_contract
    from app.workplaces.hub import hub

    wp = store.get_workplace(workplace_id)
    if not wp or wp.get("kind") != backend:
        raise RuntimeError("original remote workplace missing or changed backend")
    if backend == "tunnel":
        if not hub.is_online(workplace_id):
            raise ConnectionError("original tunnel workplace is offline")
        # Every supervised RPC carries the current immutable envelope so the
        # destination validates owner/session/generation before acting.
        # Callers run under execution_scope(durable_context(item)); a
        # revoked ceiling fails closed here instead of acting stale.
        context = store.access.revalidate(current_execution())
        if workplace_id != context.active_workplace_id:
            from app.runtime.access import AccessDenied
            raise AccessDenied("Background destination is outside the execution ceiling")
        remote_contract.ensure_admitted(wp, context)
        wire = dict(params or {})
        wire["exec_context"] = remote_contract.build_envelope(context, workplace_id)
        payload = hub.call(workplace_id, method, wire, timeout=10)
    elif backend == "ssh":
        from app.workplaces import ssh_exec

        secrets = store.get_workplace_secrets(workplace_id)
        if not secrets:
            raise RuntimeError("original SSH workplace secrets missing")
        # Invoke raw handler so timeout exceptions survive the RPC boundary.
        handler = ssh_exec._HANDLERS[method]
        try:
            return handler(dict(secrets, id=workplace_id), params)
        except ssh_exec.SSHJobRejected as exc:
            raise RemoteStartRejected(str(exc)) from exc
    else:
        raise ValueError("remote backend must be ssh or tunnel")
    if not payload.get("ok"):
        message = str(payload.get("error") or "remote background job RPC failed")
        if any(word in message.lower() for word in ("timed out", "timeout", "disconnected", "failed to send", "uncertain", "offline", "reconnect")):
            raise ConnectionError(message)
        raise RemoteStartRejected(message)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("unsupported background job response")
    return result


def _normalize(result: dict[str, Any], handle: str) -> dict[str, Any]:
    # Supervised contract v2: the destination attests owner/session admission
    # with a duration bound. The old process_contract=1 exposed broad process
    # RPCs with neither owned admission nor a destination-enforced bound, so
    # responses without the v2 attestation are rejected, never trusted.
    if result.get("remote_contract") != REMOTE_EXEC_CONTRACT:
        raise RuntimeError("unsupported background job contract; update the connector")
    if result.get("id") and result["id"] != handle:
        raise RuntimeError("remote background job handle does not match start correlation")
    status = str(result.get("status") or "unknown").lower()
    rc = result.get("returncode")
    if status == "exited":
        status = "succeeded" if rc == 0 else "failed" if isinstance(rc, int) else "unknown"
    if status in ("succeeded", "failed", "stopped") and not isinstance(rc, int):
        status = "unknown"
    if status not in ("starting", "running", "stopping", "succeeded", "failed", "stopped", "unknown"):
        status = "unknown"
    return dict(result, backend_handle=handle, handle=handle, status=status, exit_code=rc)


def start_remote(backend: str, workplace_id: str, command: str, cwd: str) -> dict[str, Any]:
    import uuid

    from app.runtime.access import AccessDenied, current_execution
    from app.services.access import access

    context = access.require_tool(current_execution(), "bash")
    if workplace_id != context.active_workplace_id:
        raise AccessDenied("Background destination is outside the execution ceiling")
    if backend == "container":
        from app.runtime.isolation import jobs

        return jobs.start(context, command, cwd)
    if backend != "tunnel":
        from app.runtime.access import AccessUnavailable

        raise AccessUnavailable("Remote background destination has no verified owned, duration-bounded supervised boundary")
    from app.services import store
    from app.workplaces import remote_contract

    wp = store.get_workplace(workplace_id)
    if not wp:
        raise RuntimeError("original remote workplace missing or changed backend")
    remote_contract.require_tunnel_destination(wp, mode=context.execution_mode)
    remote_contract.ensure_admitted(wp, context)
    handle = f"bg_{uuid.uuid4().hex[:16]}"
    try:
        duration = int(context.quota.duration_seconds or 0)
    except (TypeError, ValueError):
        duration = 0
    result = _rpc(backend, workplace_id, "process_start", {
        "correlation_id": handle,
        "command": command,
        "cwd": cwd,
        # The destination enforces its own timeout at min(requested,
        # admitted quota); the supervisor below additionally kills past
        # the quota deadline. Neither side trusts the other's clock alone.
        "timeout": duration or 300,
        "exec_context": remote_contract.build_envelope(context, workplace_id),
    })
    if result.get("remote_contract") != REMOTE_EXEC_CONTRACT:
        raise RuntimeError("unsupported background job contract; update the connector")
    # The destination assigns the job id; the coordinator's handle is the
    # idempotency correlation (retries never duplicate an uncertain start).
    if result.get("correlation_id") not in (None, "", handle):
        raise RuntimeError("remote background job correlation does not match start")
    return _normalize(result, result.get("id") or handle)


def observe_remote(backend: str, workplace_id: str, handle: str) -> dict[str, Any]:
    if backend == "container":
        from app.runtime.isolation import jobs
        from app.runtime.access import current_execution

        return jobs.observe(handle, context=current_execution(required=False))
    try:
        return _normalize(_rpc(backend, workplace_id, "process_status", {"id": handle}), handle)
    except Exception as exc:
        return {"id": handle, "backend_handle": handle, "status": "unknown", "exit_code": None, "reason": str(exc)}


def stop_remote_job(item: dict[str, Any]) -> dict[str, Any]:
    """Stop a remote background job using its stored owner/session identity.

    Killing never requires current grants: the envelope carries the job's
    recorded owner/session/destination and the destination only permits
    killing jobs tagged with that exact scope. Revocation-time stops run
    after the generation bumped, so revalidating the live ceiling here
    would fail closed on the kill itself and strand managed processes.
    """
    backend = item.get("backend")
    workplace_id = item.get("workplace_id") or ""
    handle = item.get("backend_handle") or ""
    if backend == "container":
        from app.runtime.isolation import jobs

        return jobs.stop(handle)
    if backend != "tunnel" or not workplace_id or not handle:
        raise ValueError("Remote job has no supervised destination handle")
    from app.services import store
    from app.workplaces.hub import hub

    wp = store.get_workplace(workplace_id)
    if not wp or wp.get("kind") != "tunnel":
        raise RuntimeError("original remote workplace missing or changed backend")
    stored = (item.get("execution_context") or {}) if isinstance(item.get("execution_context"), dict) else {}
    envelope = {
        "v": 1,
        "owner_user_id": item.get("user_id") or stored.get("user_id") or "",
        "session_id": item.get("session_id") or stored.get("session_id") or "",
        "agent_id": item.get("agent_id") or stored.get("agent_id") or "",
        "execution_mode": stored.get("execution_mode") or "unrestricted",
        "destination_id": workplace_id,
        "active_workplace_id": stored.get("active_workplace_id") or workplace_id,
        "access_generation": stored.get("access_generation", 0),
        "resources": [],
        "quota": {},
    }
    if not envelope["owner_user_id"] or not envelope["session_id"]:
        raise ValueError("Remote job has no owner/session identity")
    if not hub.is_online(workplace_id):
        raise ConnectionError("original tunnel workplace is offline")
    payload = hub.call(workplace_id, "process_kill",
                       {"id": handle, "exec_context": envelope}, timeout=10)
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "remote stop failed"))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("unsupported background job response")
    return _normalize(result, result.get("id") or handle)


def stop_remote(backend: str, workplace_id: str, handle: str) -> dict[str, Any]:
    if backend == "container":
        from app.runtime.isolation import jobs

        return jobs.stop(handle)
    try:
        return _normalize(_rpc(backend, workplace_id, "process_kill", {"id": handle}), handle)
    except Exception as exc:
        return {"id": handle, "backend_handle": handle, "status": "unknown", "exit_code": None, "reason": str(exc)}
