"""Destination-owned remote execution contracts (Stage 4).

A tunnel transports bytes; it does not isolate them. Every supervised remote
RPC therefore carries an immutable execution envelope that the *destination*
validates before acting:

* owner user, owning session/agent, execution mode and destination id;
* the chat-enabled resource set with permissions (execution mounts plus
  transfer-only endpoints);
* the session access generation (bumped on every grant/access change);
* a bounded quota slice (duration / concurrency).

Connectors that cannot enforce this contract (older than
:data:`MIN_EXEC_VERSION` or missing :data:`EXEC_CONTEXT_CAP`) stay
fail-closed: no local fallback, no privileged default. Restricted execution
additionally requires the destination to attest an equivalent per-chat
container boundary (:data:`REMOTE_SANDBOX_CAP` plus image); unrestricted
execution requires a matching Admin grant plus explicit chat acknowledgement
and runs under the destination OS account with supervision — never as a
general platform elevation.

Admission is negotiated once per (destination, session, generation) via the
``exec_admit`` RPC and cached only while the same live connector session
holds it. Revocation bumps the generation, which the destination rejects;
teardown is confirmed via ``exec_teardown``.
"""

from __future__ import annotations

import threading
from typing import Any

from app.runtime.access import AccessDenied, AccessUnavailable, ExecutionContext
from app.workplaces.hub import (
    EXEC_CONTEXT_CAP,
    MIN_EXEC_VERSION,
    REMOTE_SANDBOX_CAP,
    hub,
)

#: Supervised remote execution contract version. Destinations attest it in
#: every ``exec_admit`` response; anything else is unsupported.
REMOTE_EXEC_CONTRACT = 2

_ADMIT_TIMEOUT = 10.0

_lock = threading.Lock()
# (workplace_id, session_id, generation) -> (session object, attestation)
_admissions: dict[tuple[str, str, int], tuple[Any, dict[str, Any]]] = {}


def reset() -> None:
    """Test helper — drop cached admissions."""
    with _lock:
        _admissions.clear()


def connection_status(workplace_id: str) -> dict[str, Any]:
    """Live capability report for a tunnel destination (never cached)."""
    session = hub.get(workplace_id)
    if session is None:
        return {
            "online": False,
            "version": "",
            "caps": "",
            "exec_ok": False,
            "sandbox_ok": False,
        }
    return {
        "online": True,
        "version": session.version or "",
        "caps": session.caps or "",
        "exec_ok": bool(session.exec_capable),
        "sandbox_ok": bool(session.sandbox_capable),
    }


def require_tunnel_destination(
    workplace: dict[str, Any], *, mode: str, purpose: str = "execution"
) -> Any:
    """Return the live connector session after capability negotiation.

    Raises :class:`AccessUnavailable` when the destination is offline,
    runs an unsupported connector, or (for restricted work) cannot attest
    an equivalent per-chat container boundary. Never falls back locally.
    """
    wid = str(workplace.get("id") or "")
    if not wid:
        raise AccessUnavailable("Execution destination is unavailable")
    session = hub.get(wid)
    if session is None:
        raise AccessUnavailable("Execution destination is offline")
    if not session.exec_capable:
        raise AccessUnavailable(
            "Connector at this destination does not enforce supervised "
            f"execution contracts (needs {EXEC_CONTEXT_CAP}, version "
            f">= {MIN_EXEC_VERSION}); update the connector"
        )
    if mode == "restricted" and not session.sandbox_capable:
        raise AccessUnavailable(
            "Destination has no verified restricted boundary "
            f"(needs {REMOTE_SANDBOX_CAP} with the full-toolchain image)"
        )
    _ = purpose
    return session


def build_envelope(context: ExecutionContext, dest_workplace_id: str) -> dict[str, Any]:
    """Freeze the immutable execution envelope for one destination."""
    resources = [
        {
            "workplace_id": r.workplace_id,
            "permission": r.permission,
            "destination_id": r.destination_id,
            "kind": r.kind,
            "transfer_only": bool(getattr(r, "transfer_only", False)),
        }
        for r in context.resources
    ]
    return {
        "v": 1,
        "owner_user_id": context.user_id,
        "session_id": context.session_id,
        "agent_id": context.agent_id,
        "execution_mode": context.execution_mode,
        "destination_id": dest_workplace_id,
        "active_workplace_id": context.active_workplace_id,
        "access_generation": context.access_generation,
        "resources": resources,
        "quota": {
            "duration_seconds": context.quota.duration_seconds,
            "max_concurrent_jobs": context.quota.max_concurrent_jobs,
        },
    }


def ensure_admitted(
    workplace: dict[str, Any],
    context: ExecutionContext,
    *,
    timeout: float = _ADMIT_TIMEOUT,
) -> dict[str, Any]:
    """Negotiate (or reuse) destination admission for this generation.

    Revalidates the caller's context first so revoked grants fail closed
    before any bytes leave the coordinator. Returns the destination
    attestation; raises :class:`AccessDenied`/:class:`AccessUnavailable`
    when the destination rejects the envelope.
    """
    from app.services import store

    wid = str(workplace.get("id") or "")
    current = store.access.revalidate(context)
    if current.destination_id != wid and not any(
        r.workplace_id == wid for r in current.resources
    ):
        raise AccessDenied("Remote destination is outside activated execution scope")
    session = require_tunnel_destination(workplace, mode=current.execution_mode)
    envelope = build_envelope(current, wid)
    key = (wid, current.session_id, current.access_generation)
    with _lock:
        cached = _admissions.get(key)
        if cached is not None and cached[0] is session:
            return cached[1]
    payload = hub.call(wid, "exec_admit", {"exec_context": envelope}, timeout=timeout)
    if not payload.get("ok"):
        _raise_destination_refusal(payload.get("error") or "destination refused admission")
    attestation = payload.get("result")
    if not isinstance(attestation, dict) or attestation.get("contract") != REMOTE_EXEC_CONTRACT:
        raise AccessUnavailable(
            "Destination returned an unsupported execution contract; update the connector"
        )
    if str(attestation.get("destination") or "") != wid:
        raise AccessUnavailable("Destination attestation is for another workplace")
    if current.execution_mode == "restricted" and not attestation.get("sandbox"):
        raise AccessUnavailable("Destination has no verified restricted boundary")
    with _lock:
        _admissions[key] = (session, attestation)
    return attestation


def drop_admission(workplace_id: str, session_id: str, generation: int) -> None:
    with _lock:
        _admissions.pop((workplace_id, session_id, generation), None)


def teardown_destination(
    workplace_id: str,
    context: ExecutionContext,
    *,
    timeout: float = _ADMIT_TIMEOUT,
) -> bool:
    """Ask the destination to kill owner-session work and confirm.

    Returns True only when the destination acknowledges teardown. A False
    return (or raised :class:`AccessUnavailable`) must retain the caller's
    pending barrier — never report completed revocation while managed
    processes may retain access.
    """
    from app.services import store

    current = store.access.revalidate(context)
    envelope = build_envelope(current, workplace_id)
    session = hub.get(workplace_id)
    if session is None:
        return False
    payload = hub.call(
        workplace_id, "exec_teardown", {"exec_context": envelope}, timeout=timeout
    )
    if not payload.get("ok"):
        return False
    result = payload.get("result")
    if not isinstance(result, dict) or not result.get("torn_down"):
        return False
    with _lock:
        for key in [k for k in _admissions if k[0] == workplace_id and k[1] == current.session_id]:
            _admissions.pop(key, None)
    return True


def _raise_destination_refusal(message: str) -> None:
    text = str(message or "")
    lowered = text.lower()
    if any(
        word in lowered
        for word in ("stale", "generation", "revoked", "owner", "denied", "forbidden", "permission")
    ):
        raise AccessDenied("Destination refused execution: access is no longer valid")
    raise AccessUnavailable(f"Destination refused execution ({text[:160]})")


def stop_session(session_id: str) -> None:
    """Confirmed remote teardown for one session (revocation stopper).

    Runs after the access layer bumps the generation and marks pending, so
    it must NOT require current grants: killing is always safe. It builds a
    minimal teardown envelope from the session record (owner, destinations,
    current generation) and requires an acknowledgement from every online
    contract-capable tunnel destination the session used. Offline or
    unsupported destinations raise :class:`AccessUnavailable` so the caller
    retains its pending barrier instead of reporting completed revocation
    while managed processes may retain access.
    """
    from app.services import store

    session = store.get_session(session_id)
    if not session:
        return
    owner = session.get("user_id") or ""
    destinations = [w for w in dict.fromkeys(
        [session.get("workplace_id"), *(session.get("additional_workplace_ids") or [])]
    ) if w]
    if not owner or not destinations:
        return
    try:
        generation = int(session.get("access_generation") or 0)
    except (TypeError, ValueError):
        generation = 0
    failures: list[str] = []
    for wid in destinations:
        wp = store.get_workplace(wid)
        if not wp or (wp.get("kind") or "") != "tunnel":
            continue
        status = connection_status(wid)
        if not status["online"]:
            failures.append(f"{wid}: offline")
            continue
        if not status["exec_ok"]:
            failures.append(f"{wid}: unsupported connector")
            continue
        envelope = {
            "v": 1,
            "owner_user_id": owner,
            "session_id": session_id,
            "agent_id": session.get("coordinator_id") or "",
            "execution_mode": session.get("execution_mode") or "restricted",
            "destination_id": wid,
            "active_workplace_id": session.get("workplace_id") or "",
            "access_generation": generation,
            "resources": [],
            "quota": {},
        }
        payload = hub.call(wid, "exec_teardown", {"exec_context": envelope}, timeout=_ADMIT_TIMEOUT)
        result = payload.get("result") if payload.get("ok") else None
        if not isinstance(result, dict) or not result.get("torn_down"):
            failures.append(f"{wid}: unconfirmed")
            continue
        with _lock:
            for key in [k for k in _admissions if k[0] == wid and k[1] == session_id]:
                _admissions.pop(key, None)
    if failures:
        raise AccessUnavailable(
            "Remote execution teardown unconfirmed (" + "; ".join(failures) + ")"
        )


__all__ = [
    "REMOTE_EXEC_CONTRACT",
    "build_envelope",
    "connection_status",
    "drop_admission",
    "ensure_admitted",
    "require_tunnel_destination",
    "reset",
    "stop_session",
    "teardown_destination",
]
