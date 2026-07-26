"""In-process hub for live Tomo Connector WebSocket sessions.

Only a workplace with a registered socket is ``connected``. Tools call
:meth:`ConnectorHub.call` (sync-safe) to run RPC on the remote device.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
import uuid
from typing import Any

from starlette.websockets import WebSocket


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
    ) -> None:
        self.workplace_id = workplace_id
        self.websocket = websocket
        self.loop = loop
        self.hostname = hostname
        self.version = version
        self.connected_at = time.time()
        self.last_seen = self.connected_at
        self._pending: dict[str, concurrent.futures.Future[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def touch(self) -> None:
        self.last_seen = time.time()

    async def send(self, message: dict[str, Any]) -> None:
        await self.websocket.send_json(message)

    def resolve_rpc(self, req_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            fut = self._pending.pop(req_id, None)
        if fut is not None and not fut.done():
            fut.set_result(payload)

    def fail_all(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
        for _, fut in pending:
            if not fut.done():
                fut.set_result({"ok": False, "error": reason})

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
        with self._lock:
            self._pending[req_id] = fut
        msg = {
            "v": 1,
            "type": "rpc_request",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        try:
            send_fut = asyncio.run_coroutine_threadsafe(self.send(msg), self.loop)
            send_fut.result(timeout=min(10.0, timeout))
        except Exception as exc:
            with self._lock:
                self._pending.pop(req_id, None)
            return {"ok": False, "error": f"failed to send RPC: {exc}"}
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                self._pending.pop(req_id, None)
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
        self, workplace_id: str, websocket: WebSocket | None = None
    ) -> bool:
        """Drop session if present (and matches ``websocket`` when given)."""
        with self._lock:
            cur = self._sessions.get(workplace_id)
            if cur is None:
                return False
            if websocket is not None and cur.websocket is not websocket:
                return False
            del self._sessions[workplace_id]
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

__all__ = ["ConnectorHub", "ConnectorSession", "hub"]
