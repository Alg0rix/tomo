"""In-process hub for live Tomo Connector WebSocket sessions.

* Only a live socket makes a workplace ``connected``.
* Pending RPC messages are retained for **idempotent replay** after reconnect
  when the client advertises ``idempotent-replay`` (or version ≥ 0.2.0).
* Stale disconnects (superseded socket) do not tear down the newer session.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import time
import uuid
from typing import Any, Callable

from starlette.websockets import WebSocket

# Wait extra time for replay-capable clients to reconnect (90s).
DISCONNECT_GRACE = 90.0
MAX_PENDING_RPCS = 64


def _version_gte(version: str, minimum: str) -> bool:
    def parse(v: str) -> tuple[int, ...] | None:
        try:
            return tuple(int(p) for p in v.strip().split(".") if p != "")
        except (ValueError, AttributeError):
            return None

    a, b = parse(version), parse(minimum)
    if a is None or b is None:
        return False
    # Pad shorter tuple.
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a >= b


def client_supports_replay(*, caps: str = "", version: str = "") -> bool:
    caps_l = (caps or "").lower()
    if "idempotent-replay" in caps_l:
        return True
    return _version_gte(version, "0.2.0")


def client_supports_stream(caps: str = "") -> bool:
    """Connector can send ``rpc_progress`` for ``exec_bash`` with ``stream``."""
    return "exec-stream" in (caps or "").lower()


# req_id → live output sink. Module-level (not per session) so a reconnect
# that adopts pending RPCs keeps streaming into the same tool card.
_progress_sinks: dict[str, tuple[str, Callable[[str], None]]] = {}
_progress_lock = threading.Lock()


def emit_rpc_progress(workplace_id: str, req_id: str, data: str) -> None:
    """Route a live chunk to its waiting caller; only the owning workplace may."""
    with _progress_lock:
        owner, sink = _progress_sinks.get(req_id, ("", None))
    if sink is not None and owner == workplace_id:
        try:
            sink(data)
        except Exception:
            pass


class ConnectorSession:
    """One live connector bound to a workplace."""

    def __init__(
        self,
        workplace_id: str,
        websocket: WebSocket,
        loop: asyncio.AbstractEventLoop,
        *,
        hostname: str = "",
        version: str = "",
        platform: str = "",
        remote_ip: str = "",
        replay_ok: bool = False,
        stream_ok: bool = False,
        secret_broker: bool = False,
    ) -> None:
        self.workplace_id = workplace_id
        self.stream_ok = stream_ok
        self.secret_broker = secret_broker
        self.websocket = websocket
        self.loop = loop
        self.hostname = hostname
        self.version = version
        self.platform = platform
        self.remote_ip = remote_ip
        self.replay_ok = replay_ok
        self.connected_at = time.time()
        self.last_seen = self.connected_at
        # req_id → Future for callers waiting on a response
        self._pending: dict[str, concurrent.futures.Future[dict[str, Any]]] = {}
        # req_id → raw JSON text (for reconnect replay)
        self._pending_msg: dict[str, str] = {}
        self._lock = threading.Lock()

    def touch(self) -> None:
        self.last_seen = time.time()

    async def send(self, message: dict[str, Any]) -> None:
        await self.websocket.send_json(message)

    async def send_raw(self, raw: str) -> None:
        await self.websocket.send_text(raw)

    def resolve_rpc(self, req_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            fut = self._pending.pop(req_id, None)
            self._pending_msg.pop(req_id, None)
        if fut is not None:
            try:
                fut.set_result(payload)
            except concurrent.futures.InvalidStateError:
                # The caller may time out concurrently with a late response.
                pass

    def fail_all(self, reason: str) -> None:
        """Fail pending RPCs (legacy clients / hub reset)."""
        with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
            self._pending_msg.clear()
        for _, fut in pending:
            try:
                fut.set_result({"ok": False, "error": reason})
            except concurrent.futures.InvalidStateError:
                pass

    def take_pending_for_replay(self) -> list[tuple[str, str, concurrent.futures.Future]]:
        """Detach pending futures/messages so a new session can adopt them."""
        with self._lock:
            items = [
                (rid, self._pending_msg[rid], fut)
                for rid, fut in self._pending.items()
                if rid in self._pending_msg and not fut.done()
            ]
            self._pending.clear()
            self._pending_msg.clear()
        return items

    async def adopt_pending(
        self,
        items: list[tuple[str, str, concurrent.futures.Future]],
    ) -> None:
        """Adopt pending RPCs from a previous session and re-send them."""
        accepted = []
        rejected = []
        with self._lock:
            for rid, msg, fut in items:
                if fut.done():
                    continue
                if len(self._pending) >= MAX_PENDING_RPCS:
                    rejected.append(fut)
                    continue
                self._pending[rid] = fut
                self._pending_msg[rid] = msg
                accepted.append((rid, msg, fut))
        for fut in rejected:
            try:
                fut.set_result({
                    "ok": False,
                    "error": "busy: replay capacity reached; execution status uncertain; inspect effects before retrying",
                })
            except concurrent.futures.InvalidStateError:
                pass
        for rid, msg, fut in accepted:
            self._watch_pending(rid, fut)
            if fut.done():
                continue
            try:
                await asyncio.wait_for(self.send_raw(msg), timeout=5.0)
            except Exception:
                # Leave pending; caller may still time out or reconnect again.
                pass

    def _watch_pending(self, req_id: str, fut: concurrent.futures.Future) -> None:
        # The caller may be waiting through an older session after adoption.
        def discard(_: concurrent.futures.Future) -> None:
            with self._lock:
                if self._pending.get(req_id) is fut:
                    self._pending.pop(req_id, None)
                    self._pending_msg.pop(req_id, None)

        fut.add_done_callback(discard)

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Send ``rpc_request`` and wait for ``rpc_response`` (thread-safe).

        ``on_progress`` receives live ``rpc_progress`` chunks when the
        connector supports streaming; otherwise it is ignored.
        """
        req_id = uuid.uuid4().hex
        if on_progress is not None and self.stream_ok:
            params = {**(params or {}), "stream": True}
            with _progress_lock:
                _progress_sinks[req_id] = (self.workplace_id, on_progress)
        try:
            return self._call(req_id, method, params, timeout)
        finally:
            with _progress_lock:
                _progress_sinks.pop(req_id, None)

    def _call(
        self,
        req_id: str,
        method: str,
        params: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]:
        fut: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
        msg = {
            "v": 1,
            "type": "rpc_request",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        raw = json.dumps(msg, separators=(",", ":"))
        with self._lock:
            if len(self._pending) >= MAX_PENDING_RPCS:
                return {"ok": False, "error": "busy: too many pending connector RPCs"}
            self._pending[req_id] = fut
            self._pending_msg[req_id] = raw
        self._watch_pending(req_id, fut)
        send_fut = None
        try:
            send_fut = asyncio.run_coroutine_threadsafe(self.send(msg), self.loop)
            send_fut.result(timeout=min(10.0, timeout))
        except Exception as exc:
            if send_fut is not None:
                send_fut.cancel()
            if not self.replay_ok:
                with self._lock:
                    self._pending.pop(req_id, None)
                    self._pending_msg.pop(req_id, None)
                return {"ok": False, "error": f"failed to send RPC: {exc}"}
            # Replay-capable: leave pending for reconnect.

        wait = timeout + (DISCONNECT_GRACE if self.replay_ok else 0.0)
        try:
            return fut.result(timeout=wait)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            with self._lock:
                self._pending.pop(req_id, None)
                self._pending_msg.pop(req_id, None)
            return {"ok": False, "error": f"RPC timed out after {timeout:g}s"}


class ConnectorHub:
    """Process-wide map of workplace_id → live session."""

    def __init__(self) -> None:
        self._sessions: dict[str, ConnectorSession] = {}
        self._disconnected: dict[str, tuple[ConnectorSession, threading.Timer]] = {}
        self._lock = threading.RLock()

    def get(self, workplace_id: str) -> ConnectorSession | None:
        with self._lock:
            return self._sessions.get(workplace_id)

    def is_online(self, workplace_id: str) -> bool:
        return self.get(workplace_id) is not None

    def register(self, session: ConnectorSession) -> ConnectorSession | None:
        """Register session; returns previous session if replaced."""
        with self._lock:
            prev = self._sessions.get(session.workplace_id)
            detached = self._disconnected.pop(session.workplace_id, None)
            if detached is not None:
                detached[1].cancel()
                if prev is None:
                    prev = detached[0]
            self._sessions[session.workplace_id] = session
            return prev

    def unregister(
        self,
        workplace_id: str,
        websocket: WebSocket | None = None,
        *,
        fail_pending: bool | None = None,
    ) -> bool:
        """Drop session if present (and matches ``websocket`` when given).

        When the session supports replay, pending RPCs are **not** failed so a
        reconnect can re-send them (unless ``fail_pending`` forces it).
        """
        with self._lock:
            cur = self._sessions.get(workplace_id)
            if cur is None:
                return False
            if websocket is not None and cur.websocket is not websocket:
                return False
            del self._sessions[workplace_id]
            if fail_pending is None:
                fail_pending = not cur.replay_ok
            if fail_pending:
                cur.fail_all("connector disconnected")
            else:
                # Retain replay state without reporting an offline socket as online.
                def expire() -> None:
                    with self._lock:
                        detached = self._disconnected.get(workplace_id)
                        if detached is not None and detached[0] is cur:
                            del self._disconnected[workplace_id]
                            cur.fail_all("connector reconnect grace expired")

                timer = threading.Timer(DISCONNECT_GRACE, expire)
                timer.daemon = True
                self._disconnected[workplace_id] = (cur, timer)
                timer.start()
            return True

    def call(
        self,
        workplace_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        session = self.get(workplace_id)
        if session is None:
            return {"ok": False, "error": "tunnel workplace is offline"}
        return session.call(method, params, timeout=timeout, on_progress=on_progress)

    def reset(self) -> None:
        """Test helper — drop all sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
            for session, timer in self._disconnected.values():
                timer.cancel()
                sessions.append(session)
            self._disconnected.clear()
            self._sessions.clear()
        for s in sessions:
            s.fail_all("hub reset")


hub = ConnectorHub()

__all__ = [
    "ConnectorHub",
    "ConnectorSession",
    "hub",
    "client_supports_replay",
    "client_supports_stream",
    "emit_rpc_progress",
    "DISCONNECT_GRACE",
]
