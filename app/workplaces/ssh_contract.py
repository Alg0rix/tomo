"""Supervised SSH destination contract (restricted parity with tunnels).

A raw SSH login reaches a general-purpose host account: no per-chat
boundary can be verified there, so restricted SSH over a bare cwd jail
stays denied. Restricted SSH is available only through an
operator-provisioned destination agent (:mod:`app.workplaces.ssh_agent`)
that enforces the destination-owned execution envelope — owner, session,
generation, destination-owned scopes, read-only mounts, quota deadlines
and confirmed teardown — exactly like the tunnel contract in
:mod:`app.workplaces.remote_contract`.

Operator provisioning (per SSH workplace):

1. On the destination, create a base dir and write the pinned marker to
   ``<base>/.tomo-sandbox-image`` (out-of-band, e.g. the full-toolchain
   image digest). Deploy :mod:`app.workplaces.ssh_agent` as
   ``tomo-ssh-agent`` on ``PATH`` (or rely on ``python3`` stdin staging).
2. On the coordinator, set the workplace's ``ssh_sandbox_root`` (the
   destination ``<base>``) and ``ssh_sandbox_image`` (the same marker).

Admission is negotiated once per (destination, session, generation) via the
agent ``admit`` RPC and cached only while the marker still matches. Old
(unprepared: no provisioning), offline (transport failure) and
marker-mismatched destinations fail closed with no local fallback and no
raw-cwd execution.

Unrestricted SSH keeps its existing authority (matching Admin grant plus
explicit chat acknowledgement, OS-account caveats surfaced in the UI) and
does not need the agent — but restricted work never uses that path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from app.runtime.access import AccessDenied, AccessUnavailable, ExecutionContext

#: Agent contract version the coordinator accepts. Anything else is
#: unsupported (old agent / unexpected program on the remote end).
SSH_AGENT_CONTRACT = 2

_ADMIT_TIMEOUT = 15.0

_AGENT_SOURCE = str(Path(__file__).with_name("ssh_agent.py"))

_lock = threading.Lock()
# (workplace_id, session_id, generation) -> attestation dict
_admissions: dict[tuple[str, str, int], dict[str, Any]] = {}


def reset() -> None:
    """Test helper — drop cached admissions."""
    with _lock:
        _admissions.clear()


# -- transports ---------------------------------------------------------


class AgentTransportError(RuntimeError):
    pass


class SubprocessTransport:
    """Local destination subprocess running the real agent (tests/local).

    No live external host: the same stdlib agent enforces the contract
    against isolated temp dirs.
    """

    def __init__(self, root: str | Path, *, workplace_id: str = ""):
        import os as _os

        self.root = str(root)
        self.workplace_id = workplace_id
        env = dict(_os.environ)
        env["TOMO_SSH_AGENT_ROOT"] = str(root)
        if workplace_id:
            env["TOMO_SSH_AGENT_WORKPLACE"] = workplace_id
        try:
            self._proc = subprocess.Popen(
                [sys.executable, _AGENT_SOURCE],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            raise AgentTransportError(f"local destination unavailable: {exc}") from exc
        self._lock = threading.Lock()

    def call(self, op: str, params: dict[str, Any], timeout: float = _ADMIT_TIMEOUT) -> dict[str, Any]:
        import queue

        assert self._proc.stdin is not None and self._proc.stdout is not None
        box: queue.Queue[str] = queue.Queue()

        def read_line() -> None:
            try:
                box.put(self._proc.stdout.readline() or "")
            except Exception as exc:  # noqa: BLE001
                box.put(f"__transport_error__:{exc}")

        request = json.dumps({"op": op, "params": params}) + "\n"
        with self._lock:
            try:
                self._proc.stdin.write(request)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise AgentTransportError(f"destination transport failed: {exc}") from exc
            reader = threading.Thread(target=read_line, daemon=True)
            reader.start()
            reader.join(timeout)
            if reader.is_alive() or self._proc.poll() is not None:
                raise AgentTransportError("destination did not answer in time")
            try:
                line = box.get_nowait()
            except queue.Empty:
                raise AgentTransportError("destination did not answer in time")
        if line.startswith("__transport_error__:"):
            raise AgentTransportError("destination transport failed")
        try:
            payload = json.loads(line.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise AgentTransportError("destination answered unreadably") from exc
        if not isinstance(payload, dict):
            raise AgentTransportError("destination answered unreadably")
        return payload

    def close(self) -> None:
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except OSError:
            pass


class SSHTransport:
    """Supervised SSH transport: the real agent over Paramiko, no shell jail.

    The agent source is staged through ``python3 -c`` and the JSON request
    travels over stdin, so no agent pre-install or writable remote ``PATH``
    is required. Destination roots and the sandbox marker come from the
    operator-provisioned environment on the destination side.
    """

    def __init__(self, secrets: dict[str, Any], *, root: str, workplace_id: str = ""):
        self._secrets = secrets
        self._root = root
        self._workplace_id = workplace_id
        try:
            source = Path(_AGENT_SOURCE).read_text(encoding="utf-8")
        except OSError as exc:
            raise AgentTransportError(f"destination agent source unavailable: {exc}") from exc
        if len(source.encode("utf-8")) > 64 * 1024:
            raise AgentTransportError("destination agent source too large")
        self._source = source

    def call(self, op: str, params: dict[str, Any], timeout: float = _ADMIT_TIMEOUT) -> dict[str, Any]:
        from app.workplaces import ssh_exec

        try:
            client = ssh_exec.connect(self._secrets)
        except Exception as exc:
            raise AgentTransportError(f"SSH destination offline: {exc}") from exc
        to = max(1.0, min(float(timeout or _ADMIT_TIMEOUT), 120.0))
        try:
            command = (
                f"TOMO_SSH_AGENT_ROOT={_sh_quote(self._root)} "
                f"TOMO_SSH_AGENT_WORKPLACE={_sh_quote(self._workplace_id)} "
                f"python3 -c {_sh_quote(self._source)}"
            )
            _stdin, stdout, _stderr = client.exec_command(command, timeout=int(to) + 5)
            request = json.dumps({"op": op, "params": params}) + "\n"
            try:
                _stdin.write(request)
                _stdin.flush()
                _stdin.channel.shutdown_write()
            except OSError as exc:
                raise AgentTransportError(f"destination transport failed: {exc}") from exc
            import time as _time

            deadline = _time.monotonic() + to
            chunks: list[bytes] = []
            while _time.monotonic() < deadline:
                if stdout.channel.recv_ready():
                    chunk = stdout.channel.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
                elif stdout.channel.exit_status_ready() and not stdout.channel.recv_ready():
                    break
                else:
                    _time.sleep(0.02)
            else:
                raise AgentTransportError("destination did not answer in time")
            raw = b"".join(chunks).decode("utf-8", errors="replace")
            line = (raw.strip().splitlines() or [""])[0]
            try:
                payload = json.loads(line or "{}")
            except json.JSONDecodeError as exc:
                raise AgentTransportError("destination answered unreadably") from exc
            if not isinstance(payload, dict):
                raise AgentTransportError("destination answered unreadably")
            return payload
        finally:
            try:
                client.close()
            except Exception:
                pass


def _sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


# -- capability + admission ----------------------------------------------


def provisioned(workplace: dict[str, Any]) -> bool:
    """True when the operator provisioned a destination sandbox for SSH."""
    root = str(workplace.get("ssh_sandbox_root") or "").strip()
    image = str(workplace.get("ssh_sandbox_image") or "").strip()
    return bool(root and image)


def require_ssh_destination(workplace: dict[str, Any], *, mode: str) -> None:
    """Fail closed unless this SSH destination can supervise *mode*.

    Restricted additionally requires operator provisioning (sandbox root +
    pinned image marker); the marker itself is verified at admission, not
    trusted from the row alone. Unrestricted relies on the caller's
    grant + acknowledgement (OS-account caveats apply).
    """
    wid = str(workplace.get("id") or "")
    if not wid:
        raise AccessUnavailable("Execution destination is unavailable")
    if mode != "unrestricted" and not provisioned(workplace):
        raise AccessUnavailable(
            "SSH destinations have no verified restricted boundary; "
            "restricted SSH execution is unavailable"
        )


def _expected_marker(workplace: dict[str, Any]) -> str:
    return str(workplace.get("ssh_sandbox_image") or "").strip()


def ensure_admitted(
    workplace: dict[str, Any],
    context: ExecutionContext,
    transport: Any,
    *,
    timeout: float = _ADMIT_TIMEOUT,
) -> dict[str, Any]:
    """Negotiate (or reuse) destination admission for this generation.

    Revalidates the caller's context first so revoked grants fail closed
    before any bytes leave the coordinator. Returns the destination
    attestation; raises when the destination rejects the envelope, attests
    an old contract, or (for restricted work) cannot prove the provisioned
    sandbox marker.
    """
    from app.services import store
    from app.workplaces import remote_contract

    context = store.access.revalidate(context)
    wid = str(workplace.get("id") or "")
    if not wid:
        raise AccessUnavailable("Execution destination is unavailable")
    key = (wid, context.session_id, int(context.access_generation))
    with _lock:
        cached = _admissions.get(key)
    if cached and cached.get("marker") == _expected_marker(workplace):
        return cached
    envelope = remote_contract.build_envelope(context, wid)
    try:
        payload = transport.call(
            "admit", {"exec_context": envelope, "destination_id": wid}, timeout=timeout
        )
    except AgentTransportError as exc:
        raise AccessUnavailable(f"SSH destination is offline or unprepared: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        err = str((payload or {}).get("error") or "destination refused admission")
        raise AccessDenied(f"SSH destination refused execution: {err[:200]}")
    result = payload.get("result") or {}
    if int(result.get("contract", 0) or 0) != SSH_AGENT_CONTRACT:
        raise AccessUnavailable("SSH destination agent is unsupported; update the destination agent")
    attestation = {
        "contract": SSH_AGENT_CONTRACT,
        "sandbox": bool(result.get("sandbox")),
        "marker": str(result.get("image") or ""),
        "scopes": list(result.get("scopes") or []),
    }
    if context.execution_mode == "restricted":
        expected = _expected_marker(workplace)
        if not attestation["sandbox"] or attestation["marker"] != expected:
            raise AccessUnavailable(
                "SSH destination has no verified restricted boundary "
                "(sandbox marker mismatch or missing)"
            )
    with _lock:
        _admissions[key] = attestation
    return attestation


_SUPPORTED_AGENT_METHODS = {
    "exec_bash": "exec",
    "bash": "exec",
    "exec_python": None,  # agent exec runs bash; python via `python3 -c` script text
    "read_file": "read",
    "write_file": "write",
    "read_file_b64": "read_b64",
    "write_file_b64": "write_b64",
}


def call_via_agent(
    workplace: dict[str, Any],
    context: ExecutionContext,
    method: str,
    params: dict[str, Any],
    transport: Any,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run one RPC through the supervised destination agent."""
    from app.workplaces import remote_contract

    op = _SUPPORTED_AGENT_METHODS.get(method, "unsupported")
    if op is None and method == "exec_python":
        op = "exec"
    if op == "unsupported" or op is None:
        return {"ok": False, "error": f"{method} is unavailable over supervised SSH"}
    wid = str(workplace.get("id") or "")
    ensure_admitted(workplace, context, transport)
    envelope = remote_contract.build_envelope(context, wid)
    wire = dict(params or {})
    if method == "exec_python" and isinstance(wire.get("code"), str):
        import shlex as _shlex

        wire = {"script": f"python3 -c {_shlex.quote(wire['code'])}",
                "timeout": wire.get("timeout", timeout), "cwd": wire.get("cwd", "")}
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
    wire["exec_context"] = envelope
    wire["destination_id"] = wid
    try:
        payload = transport.call(op, wire, timeout=float(wire.get("timeout", timeout)) + 10.0)
    except AgentTransportError as exc:
        return {"ok": False, "error": f"SSH destination call failed: {exc}"}
    if not isinstance(payload, dict) or "ok" not in payload:
        return {"ok": False, "error": "SSH destination answered unreadably"}
    return payload


def stop_session_production(session_id: str) -> None:
    """Confirmed SSH teardown using stored secrets (revocation stopper)."""
    from app.services import store

    def factory(workplace: dict[str, Any]):
        wid = str(workplace.get("id") or "")
        secrets = store.get_workplace_secrets(wid) if wid else None
        if not secrets:
            raise AgentTransportError("SSH workplace secrets not found")
        root = str(workplace.get("ssh_sandbox_root") or "").strip()
        if not root:
            raise AgentTransportError("SSH destination has no provisioned sandbox")
        return SSHTransport(secrets, root=root, workplace_id=wid)

    stop_session(session_id, transport_factory=factory)


def stop_session(session_id: str, transport_factory=None) -> None:
    """Confirmed SSH teardown for one session (revocation stopper).

    Best-effort per destination: unconfirmed kills raise so the caller
    retains its pending barrier. ``transport_factory`` builds a transport
    for a workplace (production: SSHTransport from stored secrets);
    without it, SSH destinations with cached admissions are dropped
    locally and reported unconfirmed.
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
        if not wp or (wp.get("kind") or "") != "ssh":
            continue
        with _lock:
            had_admission = any(k[0] == wid and k[1] == session_id for k in _admissions)
        if not had_admission:
            # Nothing was ever admitted here in this process: no managed
            # SSH work can exist to confirm. (Process restarts lose the
            # cache exactly like the tunnel contract.)
            continue
        if transport_factory is None:
            with _lock:
                for k in [k for k in _admissions if k[0] == wid and k[1] == session_id]:
                    _admissions.pop(k, None)
            failures.append(f"{wid}: unconfirmed")
            continue
        try:
            transport = transport_factory(wp)
        except Exception as exc:
            failures.append(f"{wid}: {exc}")
            continue
        envelope = {
            "v": 1, "owner_user_id": owner, "session_id": session_id,
            "agent_id": session.get("coordinator_id") or "",
            "execution_mode": session.get("execution_mode") or "restricted",
            "destination_id": wid,
            "active_workplace_id": session.get("workplace_id") or "",
            "access_generation": generation,
            "resources": [], "quota": {},
        }
        try:
            payload = transport.call(
                "teardown", {"exec_context": envelope, "destination_id": wid},
                timeout=_ADMIT_TIMEOUT,
            )
        except AgentTransportError as exc:
            failures.append(f"{wid}: {exc}")
            continue
        result = payload.get("result") if payload.get("ok") else None
        if not isinstance(result, dict) or not result.get("torn_down"):
            failures.append(f"{wid}: unconfirmed")
            continue
        with _lock:
            for k in [k for k in _admissions if k[0] == wid and k[1] == session_id]:
                _admissions.pop(k, None)
    if failures:
        raise AccessUnavailable(
            "SSH execution teardown unconfirmed (" + "; ".join(failures) + ")"
        )


def destination_status(workplace: dict[str, Any]) -> dict[str, Any]:
    """Verified capability report for an SSH destination (no probing).

    ``verified`` is true only after a completed ``admit`` handshake for this
    destination with the currently pinned marker — never from the stored
    flag alone. Listing views must not trigger network probes.
    """
    wid = str(workplace.get("id") or "")
    expected = _expected_marker(workplace)
    verified = False
    if wid and expected:
        with _lock:
            verified = any(
                k[0] == wid and a.get("marker") == expected and a.get("sandbox")
                for k, a in _admissions.items()
            )
    return {"provisioned": provisioned(workplace), "verified": verified}


__all__ = [
    "SSH_AGENT_CONTRACT",
    "SSHTransport",
    "SubprocessTransport",
    "AgentTransportError",
    "call_via_agent",
    "destination_status",
    "ensure_admitted",
    "provisioned",
    "require_ssh_destination",
    "reset",
    "stop_session",
]
