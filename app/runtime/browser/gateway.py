"""Browser Gateway — pending client tool calls + live WebSocket sessions.

Mirrors the connector hub pattern: tool backends (sync, via ``to_thread``)
block on a Future while the authenticated Tomo web client executes the tool
through the Chrome extension and posts the result back.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
import time
from typing import Any

from starlette.websockets import WebSocket

from app.runtime.browser import audit as browser_audit
from app.runtime.browser.protocol import (
    ERR_BROWSER_DISCONNECTED,
    ERR_CAPABILITY_NOT_SUPPORTED,
    ERR_TIMEOUT,
    TYPE_HEARTBEAT,
    TYPE_TOOL_CANCEL,
    TYPE_TOOL_ERROR,
    TYPE_TOOL_EXECUTE,
    TYPE_TOOL_RESULT,
    envelope,
    error_result,
    new_id,
)
from app.runtime.browser.session import BrowserSession, get_session_store
from app.runtime.browser.tools_meta import CAPABILITY_BY_TOOL, is_browser_tool

logger = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT_S = 30.0


class BrowserClientLink:
    """One live WebSocket bound to a browser session."""

    def __init__(
        self,
        session: BrowserSession,
        websocket: WebSocket,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.session = session
        self.websocket = websocket
        self.loop = loop
        self.connected_at = time.time()
        self._pending: dict[str, concurrent.futures.Future[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def touch(self) -> None:
        self.session.touch()

    async def send(self, message: dict[str, Any]) -> None:
        await self.websocket.send_json(message)

    def resolve(self, call_id: str, result: dict[str, Any]) -> bool:
        with self._lock:
            fut = self._pending.pop(call_id, None)
        if fut is not None and not fut.done():
            fut.set_result(result if isinstance(result, dict) else {"success": False, "error": {"code": "BAD_RESULT", "message": "invalid result"}})
            return True
        return False

    def fail_all(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.items())
            self._pending.clear()
        err = error_result(ERR_BROWSER_DISCONNECTED, reason)
        for _, fut in pending:
            if not fut.done():
                fut.set_result(err)

    def cancel(self, call_id: str) -> None:
        with self._lock:
            fut = self._pending.pop(call_id, None)
        if fut is not None and not fut.done():
            fut.set_result(
                error_result("CANCELLED", f"Browser tool call {call_id} cancelled")
            )

    def call(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Dispatch ``browser.tool.execute`` and wait for result (thread-safe)."""
        call_id = new_id("call")
        fut: concurrent.futures.Future[dict[str, Any]] = concurrent.futures.Future()
        msg = envelope(
            TYPE_TOOL_EXECUTE,
            session_id=self.session.id,
            payload={
                "call_id": call_id,
                "tool": tool,
                "arguments": arguments if isinstance(arguments, dict) else {},
            },
        )
        # Flat fields for frontend convenience (also inside payload).
        msg["call_id"] = call_id
        msg["tool"] = tool
        msg["arguments"] = arguments if isinstance(arguments, dict) else {}

        with self._lock:
            self._pending[call_id] = fut
        try:
            send_fut = asyncio.run_coroutine_threadsafe(self.send(msg), self.loop)
            send_fut.result(timeout=min(10.0, timeout))
        except Exception as exc:
            with self._lock:
                self._pending.pop(call_id, None)
            return error_result(
                ERR_BROWSER_DISCONNECTED,
                f"Failed to dispatch browser tool: {exc}",
            )

        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            with self._lock:
                self._pending.pop(call_id, None)
            # Best-effort cancel notice to client.
            try:
                cancel_msg = envelope(
                    TYPE_TOOL_CANCEL,
                    session_id=self.session.id,
                    payload={"call_id": call_id},
                )
                cancel_msg["call_id"] = call_id
                asyncio.run_coroutine_threadsafe(self.send(cancel_msg), self.loop)
            except Exception:
                pass
            return error_result(
                ERR_TIMEOUT,
                f"Browser tool '{tool}' timed out after {timeout:g}s",
                recoverable=True,
                suggested_action="browser_snapshot",
            )


class BrowserGateway:
    """Process-wide browser session + pending call registry."""

    def __init__(self) -> None:
        self._links: dict[str, BrowserClientLink] = {}  # session_id → link
        self._user_links: dict[str, str] = {}  # user_id → session_id
        self._lock = threading.RLock()
        self._sessions = get_session_store()

    # ── link lifecycle ──────────────────────────────────────────────

    def register_link(self, link: BrowserClientLink) -> BrowserClientLink | None:
        """Attach WebSocket link; returns previous link if replaced."""
        with self._lock:
            sid = link.session.id
            prev = self._links.get(sid)
            if prev is not None and prev is not link:
                prev.fail_all("Browser client replaced")
            self._links[sid] = link
            self._user_links[link.session.user_id] = sid
            link.session.mark_connected()
            return prev

    def unregister_link(
        self,
        session_id: str,
        *,
        websocket: WebSocket | None = None,
    ) -> None:
        with self._lock:
            cur = self._links.get(session_id)
            if cur is None:
                return
            if websocket is not None and cur.websocket is not websocket:
                return
            self._links.pop(session_id, None)
            uid = cur.session.user_id
            if self._user_links.get(uid) == session_id:
                self._user_links.pop(uid, None)
            cur.session.mark_disconnected()
            cur.fail_all("Browser client disconnected")

    def get_link(self, session_id: str) -> BrowserClientLink | None:
        with self._lock:
            return self._links.get(session_id)

    def get_link_for_user(self, user_id: str) -> BrowserClientLink | None:
        with self._lock:
            sid = self._user_links.get(user_id)
            if not sid:
                return None
            return self._links.get(sid)

    def is_connected(self, user_id: str) -> bool:
        link = self.get_link_for_user(user_id)
        if link is None:
            return False
        return link.session.status == "connected" and link.session.ws_linked

    def capabilities_for_user(self, user_id: str) -> set[str]:
        link = self.get_link_for_user(user_id)
        if link is None:
            s = self._sessions.get_for_user(user_id)
            return set(s.capabilities) if s and s.status == "connected" else set()
        return set(link.session.capabilities)

    def session_for_user(self, user_id: str) -> BrowserSession | None:
        link = self.get_link_for_user(user_id)
        if link is not None:
            return link.session
        return self._sessions.get_for_user(user_id)

    # ── inbound messages ────────────────────────────────────────────

    def handle_client_message(self, session_id: str, message: dict[str, Any]) -> None:
        """Process a JSON message from the Tomo web bridge."""
        link = self.get_link(session_id)
        if link is None:
            return
        link.touch()
        msg_type = str(message.get("type") or "")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            payload = message  # allow flat messages

        if msg_type == TYPE_HEARTBEAT:
            return

        if msg_type in {TYPE_TOOL_RESULT, TYPE_TOOL_ERROR}:
            call_id = str(
                message.get("call_id")
                or payload.get("call_id")
                or ""
            ).strip()
            if not call_id:
                return
            if msg_type == TYPE_TOOL_ERROR:
                result = error_result(
                    str(payload.get("code") or message.get("code") or "TOOL_ERROR"),
                    str(payload.get("message") or message.get("message") or "tool error"),
                    recoverable=bool(payload.get("recoverable", False)),
                )
            else:
                raw = payload.get("result", message.get("result", payload))
                result = raw if isinstance(raw, dict) else {"success": True, "data": raw}
            link.resolve(call_id, result)
            return

        if msg_type == "browser.tabs.updated" or msg_type == "browser.tabs":
            tabs = payload.get("tabs") or message.get("tabs") or []
            if isinstance(tabs, list):
                link.session.set_tabs(tabs)
            return

        if msg_type == "browser.capabilities":
            caps = payload.get("capabilities") or message.get("capabilities")
            if isinstance(caps, dict):
                link.session.capabilities = {
                    k for k, v in caps.items() if v
                }
            elif isinstance(caps, list):
                link.session.capabilities = {str(c) for c in caps}
            return

    # ── tool execution (called from tool backends) ──────────────────

    def execute(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        user_id: str | None = None,
        agent_id: str = "",
        conversation_id: str = "",
        timeout: float = DEFAULT_TOOL_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Run a browser tool on the user's connected client.

        Returns a structured result dict (``success`` / ``error``).
        """
        if not is_browser_tool(tool):
            return error_result(
                ERR_CAPABILITY_NOT_SUPPORTED,
                f"Unknown browser tool: {tool}",
            )
        uid = (user_id or "").strip()
        if not uid:
            from app.runtime.browser.context import current_browser_user_id

            uid = (current_browser_user_id() or "").strip()
        if not uid:
            return error_result(
                ERR_BROWSER_DISCONNECTED,
                "No browser user context for this turn",
                recoverable=True,
            )

        link = self.get_link_for_user(uid)
        if link is None:
            return error_result(
                ERR_BROWSER_DISCONNECTED,
                "Browser is not connected. Install/open Tomo Browser extension "
                "and connect from the web app.",
                recoverable=True,
            )

        required = CAPABILITY_BY_TOOL.get(tool)
        if required and required not in link.session.capabilities:
            return error_result(
                ERR_CAPABILITY_NOT_SUPPORTED,
                f"Capability '{required}' is not available for this browser session",
                recoverable=False,
            )

        args = arguments if isinstance(arguments, dict) else {}
        # Server-side navigate guard (extension also enforces).
        if tool == "browser_navigate":
            from app.runtime.browser.permissions import check_navigate_url

            blocked = check_navigate_url(str(args.get("url") or ""))
            if blocked is not None:
                return blocked

        t0 = time.time()
        result = link.call(tool, args, timeout=timeout)
        duration_ms = (time.time() - t0) * 1000.0
        ok = bool(isinstance(result, dict) and result.get("success"))
        err_code = ""
        if isinstance(result, dict) and not ok:
            err = result.get("error") or {}
            if isinstance(err, dict):
                err_code = str(err.get("code") or "")
        browser_audit.record_execution(
            user_id=uid,
            conversation_id=conversation_id,
            agent_id=agent_id,
            browser_session_id=link.session.id,
            tool=tool,
            arguments=args,
            result=result if isinstance(result, dict) else None,
            duration_ms=duration_ms,
            status="succeeded" if ok else "failed",
            error_code=err_code,
        )
        return result if isinstance(result, dict) else error_result(
            "BAD_RESULT", "Browser client returned a non-object result"
        )

    def cancel_all_for_user(self, user_id: str) -> None:
        link = self.get_link_for_user(user_id)
        if link is not None:
            link.fail_all("Turn cancelled")

    def public_status(self, user_id: str) -> dict[str, Any]:
        session = self.session_for_user(user_id)
        if session is None:
            return {
                "status": "not_connected",
                "connected": False,
                "capabilities": [],
                "authorized_tabs": [],
            }
        data = session.to_public()
        data["connected"] = self.is_connected(user_id)
        if not data["connected"] and data["status"] == "connected":
            data["status"] = "disconnected"
        return data


_gateway: BrowserGateway | None = None
_gateway_lock = threading.Lock()


def get_gateway() -> BrowserGateway:
    global _gateway
    if _gateway is None:
        with _gateway_lock:
            if _gateway is None:
                _gateway = BrowserGateway()
    return _gateway


def reset_gateway() -> None:
    """Test helper."""
    global _gateway
    with _gateway_lock:
        _gateway = BrowserGateway()


def result_to_tool_text(result: dict[str, Any]) -> str:
    """Convert gateway result dict into the string the agent loop expects."""
    if not isinstance(result, dict):
        return "Error: invalid browser result"
    if result.get("success"):
        # Prefer human-readable snapshot text when present.
        if isinstance(result.get("snapshot"), str):
            parts = []
            tab = result.get("tab") or result.get("page")
            if isinstance(tab, dict):
                title = tab.get("title") or ""
                url = tab.get("url") or ""
                tid = tab.get("id") or ""
                parts.append(f"tab: {tid}  {title}  {url}".strip())
            parts.append(result["snapshot"])
            return "\n".join(p for p in parts if p)
        if isinstance(result.get("tabs"), list):
            allow = result.get("allow_all")
            open_n = result.get("open_count")
            head = "Authorized tabs"
            meta_bits = []
            if allow is not None:
                meta_bits.append(f"allow_all={bool(allow)}")
            if open_n is not None:
                meta_bits.append(f"open={open_n}")
            meta_bits.append(f"listed={len(result['tabs'])}")
            if meta_bits:
                head += " (" + ", ".join(meta_bits) + ")"
            lines = [head + ":"]
            for t in result["tabs"]:
                if not isinstance(t, dict):
                    continue
                lines.append(
                    f"  [{t.get('id')}] {t.get('title') or '(untitled)'}  {t.get('url') or ''}"
                )
            if len(lines) == 1:
                lines.append(
                    "  (none — open the extension popup → Resync all tabs, "
                    "or enable Control all tabs)"
                )
            return "\n".join(lines)
        if isinstance(result.get("text"), str):
            return result["text"]
        if isinstance(result.get("image_base64"), str):
            meta = result.get("page") or result.get("tab") or {}
            return (
                "Screenshot captured"
                + (f" ({meta.get('url')})" if isinstance(meta, dict) and meta.get("url") else "")
                + f"\n[image base64 length={len(result['image_base64'])}]"
            )
        # Generic success dump (compact JSON).
        slim = {k: v for k, v in result.items() if k != "success"}
        try:
            return json.dumps(slim, ensure_ascii=False, indent=2)
        except Exception:
            return "OK"
    err = result.get("error") or {}
    if isinstance(err, dict):
        code = err.get("code") or "ERROR"
        msg = err.get("message") or "browser tool failed"
        hint = err.get("suggested_action")
        text = f"Error: [{code}] {msg}"
        if hint:
            text += f" (try: {hint})"
        return text
    return f"Error: {result}"
