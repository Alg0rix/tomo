"""In-memory browser control sessions (V1).

Sessions are process-local (like the connector hub). Persistence of
authorised tabs is optional metadata mirrored from the extension for the
agent context and UI; native Chrome tab ids never leave the extension.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.runtime.browser.protocol import DEFAULT_CAPABILITIES, new_id

# Default session TTL (4 hours).
SESSION_TTL_S = 4 * 60 * 60
# Disconnect if no heartbeat within this window.
HEARTBEAT_TIMEOUT_S = 90.0


@dataclass
class BrowserTab:
    """Agent-facing tab (virtual id only — no Chrome tabId)."""

    id: str
    title: str = ""
    url: str = ""
    domain: str = ""
    authorized: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "authorized": self.authorized,
        }


@dataclass
class BrowserSession:
    id: str
    user_id: str
    client_id: str
    extension_version: str = "0.1.0"
    capabilities: set[str] = field(default_factory=lambda: set(DEFAULT_CAPABILITIES))
    authorized_tabs: dict[str, BrowserTab] = field(default_factory=dict)
    status: str = "connecting"  # connecting | connected | disconnected | expired
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    # Live WebSocket transport is owned by the gateway; session just tracks link.
    ws_linked: bool = False

    def __post_init__(self) -> None:
        if not self.expires_at:
            self.expires_at = self.created_at + SESSION_TTL_S
        if not isinstance(self.capabilities, set):
            self.capabilities = set(self.capabilities)

    def touch(self) -> None:
        self.last_seen_at = time.time()

    def is_expired(self, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        return t >= self.expires_at

    def is_stale(self, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        if self.is_expired(t):
            return True
        if self.status == "connected" and (t - self.last_seen_at) > HEARTBEAT_TIMEOUT_S:
            return True
        return False

    def mark_connected(self) -> None:
        self.status = "connected"
        self.ws_linked = True
        self.touch()

    def mark_disconnected(self) -> None:
        self.status = "disconnected"
        self.ws_linked = False
        self.touch()

    def set_tabs(self, tabs: list[dict[str, Any]]) -> None:
        """Replace authorized tab map from extension/frontend report."""
        new_map: dict[str, BrowserTab] = {}
        for raw in tabs:
            if not isinstance(raw, dict):
                continue
            tid = str(raw.get("id") or "").strip()
            if not tid:
                continue
            url = str(raw.get("url") or "")
            domain = str(raw.get("domain") or "")
            if not domain and "://" in url:
                try:
                    domain = url.split("://", 1)[1].split("/", 1)[0]
                except Exception:
                    domain = ""
            new_map[tid] = BrowserTab(
                id=tid,
                title=str(raw.get("title") or ""),
                url=url,
                domain=domain,
                authorized=bool(raw.get("authorized", True)),
            )
        self.authorized_tabs = new_map
        self.touch()

    def to_public(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "user_id": self.user_id,
            "client_id": self.client_id,
            "status": self.status,
            "extension_version": self.extension_version,
            "capabilities": sorted(self.capabilities),
            "authorized_tabs": [t.to_dict() for t in self.authorized_tabs.values()],
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "expires_at": self.expires_at,
            "connected": self.status == "connected" and self.ws_linked,
        }


class BrowserSessionStore:
    """Process-wide map of browser sessions keyed by session id and user."""

    def __init__(self) -> None:
        self._by_id: dict[str, BrowserSession] = {}
        # user_id → active session_id (one primary session per user for V1)
        self._by_user: dict[str, str] = {}
        self._lock = threading.RLock()

    def create(
        self,
        *,
        user_id: str,
        client_id: str,
        extension_version: str = "0.1.0",
        capabilities: list[str] | None = None,
    ) -> BrowserSession:
        with self._lock:
            self._gc_locked()
            sid = new_id("brs")
            caps = set(capabilities) if capabilities else set(DEFAULT_CAPABILITIES)
            # Only keep known capabilities.
            caps &= set(DEFAULT_CAPABILITIES) | caps
            session = BrowserSession(
                id=sid,
                user_id=user_id,
                client_id=client_id or new_id("client"),
                extension_version=extension_version or "0.1.0",
                capabilities=caps or set(DEFAULT_CAPABILITIES),
            )
            # Replace previous primary session for this user.
            prev_id = self._by_user.get(user_id)
            if prev_id and prev_id in self._by_id:
                old = self._by_id.pop(prev_id)
                old.mark_disconnected()
            self._by_id[sid] = session
            self._by_user[user_id] = sid
            return session

    def get(self, session_id: str) -> BrowserSession | None:
        with self._lock:
            s = self._by_id.get(session_id)
            if s is None:
                return None
            if s.is_expired():
                self._drop_locked(session_id)
                return None
            return s

    def get_for_user(self, user_id: str) -> BrowserSession | None:
        with self._lock:
            self._gc_locked()
            sid = self._by_user.get(user_id)
            if not sid:
                return None
            s = self._by_id.get(sid)
            if s is None:
                self._by_user.pop(user_id, None)
                return None
            if s.is_stale():
                s.mark_disconnected()
            return s

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._drop_locked(session_id)

    def _drop_locked(self, session_id: str) -> None:
        s = self._by_id.pop(session_id, None)
        if s is None:
            return
        s.mark_disconnected()
        if self._by_user.get(s.user_id) == session_id:
            self._by_user.pop(s.user_id, None)

    def _gc_locked(self) -> None:
        now = time.time()
        dead = [sid for sid, s in self._by_id.items() if s.is_expired(now)]
        for sid in dead:
            self._drop_locked(sid)

    def list_for_user(self, user_id: str) -> list[BrowserSession]:
        s = self.get_for_user(user_id)
        return [s] if s else []


# Process singleton
_store = BrowserSessionStore()


def get_session_store() -> BrowserSessionStore:
    return _store


def reset_session_store() -> None:
    """Test helper — clear all sessions."""
    global _store
    _store = BrowserSessionStore()
