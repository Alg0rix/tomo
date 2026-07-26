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
from typing import Any

from starlette.websockets import WebSocket

# Wait extra time for replay-capable clients to reconnect (90s).
DISCONNECT_GRACE = 90.0


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
    ) -> None:
        self.workplace_id = workplace_id
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
        if fut is not None and not fut.done():
            fut.set_result(payload)

    def fail_all(self, reason: str) -> None:
        """Fail pending RPCs (legacy clients / hub reset)."""
        with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
            self._pending_msg.clear()
        for _, fut in pending:
            if not fut.done():
                fut.set_result({"ok": False, "error": reason})

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

    def adopt_pending(
        self,
        items: list[tuple[str, str, concurrent.futures.Future]],
    ) -> None:
        """Adopt pending RPCs from a previous session and re-send them."""
        with self._lock:
            for rid, msg, fut in items:
                self._pending[rid] = fut
                self._pending_msg[rid] = msg
        for rid, msg, _ in items:
            try:
                send_fut = asyncio.run_coroutine_threadsafe(
                    self.send_raw(msg), self.loop
                )
                send_fut.result(timeout=5.0)
            except Exception:
                # Leave pending; caller may still time out.
                pass

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Send ``rpc_request`` and wait for ``rpc_response`` (thread-safe)."""
        req_id = uuid.uuid4().hex
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
            self._pending[req_id] = fut
            self._pending_msg[req_id] = raw
        try:
            send_fut = asyncio.run_coroutine_threadsafe(self.send(msg), self.loop)
            send_fut.result(timeout=min(10.0, timeout))
        except Exception as exc:
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
            with self._lock:
                self._pending.pop(req_id, None)
                self._pending_msg.pop(req_id, None)
            return {"ok": False, "error": f"RPC timed out after {timeout:g}s"}


class ConnectorHub:
    """Process-wide map of workplace_id → live session."""

    def __init__(self) -> None:
        self._sessions: dict[str, ConnectorSession] = {}
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
            return True

    def call(
        self,
        workplace_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        session = self.get(workplace_id)
        if session is None:
            return {"ok": False, "error": "tunnel workplace is offline"}
        return session.call(method, params, timeout=timeout)

    def reset(self) -> None:
        """Test helper — drop all sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s.fail_all("hub reset")


hub = ConnectorHub()

__all__ = [
    "ConnectorHub",
    "ConnectorSession",
    "hub",
    "client_supports_replay",
    "DISCONNECT_GRACE",
]
