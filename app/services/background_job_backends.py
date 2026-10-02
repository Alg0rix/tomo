"""Structured background-job RPCs pinned to the workplace captured at start."""

from __future__ import annotations

import uuid
from typing import Any


class RemoteStartRejected(RuntimeError):
    """Backend confirmed that execution was rejected before creating a job."""


class RemoteStartUnknown(RuntimeError):
    """Start may have executed; retain the correlation handle, never retry it."""

    def __init__(self, message: str, handle: str) -> None:
        super().__init__(message)
        self.handle = self.backend_handle = handle


def resolve_backend(workplace_hint: str | None = None) -> tuple[str, str, str]:
    from app.runtime.tools import workplace_ctx, workplace_remote
    from app.runtime.tools.sandbox import current_agent_id, resolve_work_root
    from app.services import store

    hint = workplace_hint or workplace_ctx.current_workplace_hint()
    if hint:
        agent = store.get_agent(current_agent_id())
        allowed = workplace_remote._agent_allowed_workplaces(agent) if agent else []
        wp = workplace_ctx.match_workplace(allowed, hint)
        if not wp:
            raise ValueError("workplace not found or not allowed")
    elif workplace_ctx.current_workplace_id():
        wp = store.get_workplace(workplace_ctx.current_workplace_id())
        if not wp:
            raise ValueError("bound workplace no longer exists")
    else:
        wp = workplace_remote.resolve_agent_workplace()
    if not wp:
        return "local", "", str(resolve_work_root())
    backend = (wp.get("kind") or "local").strip().lower()
    if backend not in ("local", "ssh", "tunnel"):
        raise ValueError("unsupported background job backend")
    if backend == "local":
        token = workplace_ctx._workplace_hint.set(hint) if hint else None
        try:
            return backend, str(wp["id"]), str(resolve_work_root())
        finally:
            if token is not None:
                workplace_ctx._workplace_hint.reset(token)
    return backend, str(wp["id"]), str(wp.get("root_path") or ".")


def _rpc(backend: str, workplace_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.services import store
    from app.workplaces.hub import hub

    wp = store.get_workplace(workplace_id)
    if not wp or wp.get("kind") != backend:
        raise RuntimeError("original remote workplace missing or changed backend")
    if backend == "tunnel":
        if not hub.is_online(workplace_id):
            raise ConnectionError("original tunnel workplace is offline")
        payload = hub.call(workplace_id, method, params, timeout=10)
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
    if result.get("process_contract") != 1:
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
    try:
        contract = _rpc(backend, workplace_id, "process_status", {"id": "__contract__"})
    except (ConnectionError, TimeoutError, OSError):
        raise
    except Exception as exc:
        raise RuntimeError(f"unsupported background job contract: {exc}") from exc
    if contract.get("process_contract") != 1:
        raise RuntimeError("unsupported background job contract; update the connector")
    handle = ("ssh_" if backend == "ssh" else "job_") + uuid.uuid4().hex
    try:
        result = _rpc(backend, workplace_id, "process_start", {"id": handle, "command": command, "cwd": cwd})
    except RemoteStartRejected:
        raise
    except Exception as exc:
        raise RemoteStartUnknown(str(exc), handle) from exc
    try:
        return _normalize(result, handle)
    except Exception as exc:
        raise RemoteStartUnknown(str(exc), handle) from exc


def observe_remote(backend: str, workplace_id: str, handle: str) -> dict[str, Any]:
    try:
        return _normalize(_rpc(backend, workplace_id, "process_status", {"id": handle}), handle)
    except Exception as exc:
        return {"id": handle, "backend_handle": handle, "status": "unknown", "exit_code": None, "reason": str(exc)}


def stop_remote(backend: str, workplace_id: str, handle: str) -> dict[str, Any]:
    try:
        return _normalize(_rpc(backend, workplace_id, "process_kill", {"id": handle}), handle)
    except Exception as exc:
        return {"id": handle, "backend_handle": handle, "status": "unknown", "exit_code": None, "reason": str(exc)}
