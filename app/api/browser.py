"""Browser Control API — sessions, status, WebSocket bridge.

* ``POST /api/browser/sessions`` — create session after extension PING/PONG
* ``GET  /api/browser/status`` — current connection + authorized tabs
* ``POST /api/browser/sessions/{id}/tabs`` — sync authorized tabs from client
* ``DELETE /api/browser/sessions/{id}`` — close session
* ``WS   /api/browser/ws`` — tool execute/result channel (cookie auth)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.deps import AuthDep, session_user_id
from app.runtime.browser.gateway import BrowserClientLink, get_gateway
from app.runtime.browser.protocol import TYPE_HEARTBEAT, envelope
from app.runtime.browser.session import get_session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/browser", tags=["browser"])


class CreateSessionIn(BaseModel):
    client_id: str = ""
    extension_version: str = "0.1.0"
    capabilities: list[str] = Field(default_factory=list)


class TabsSyncIn(BaseModel):
    tabs: list[dict[str, Any]] = Field(default_factory=list)


def _expires_iso(ts: float) -> str:
    return (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@router.post("/sessions")
async def create_browser_session(
    body: CreateSessionIn,
    request: Request,
    _: AuthDep = None,
) -> dict[str, Any]:
    """Handshake: frontend detected the extension; create a browser session."""
    user_id = session_user_id(request)
    store = get_session_store()
    session = store.create(
        user_id=user_id,
        client_id=body.client_id or "",
        extension_version=body.extension_version or "0.1.0",
        capabilities=body.capabilities or None,
    )
    return {
        "session_id": session.id,
        "expires_at": _expires_iso(session.expires_at),
        "capabilities": sorted(session.capabilities),
    }


@router.get("/status")
async def browser_status(request: Request, _: AuthDep = None) -> dict[str, Any]:
    user_id = session_user_id(request)
    gw = get_gateway()
    status = gw.public_status(user_id)
    # Config for frontend extension discovery.
    from app.core import config

    status["extension_id"] = getattr(config, "BROWSER_EXTENSION_ID", "") or ""
    status["extension_download_url"] = (
        getattr(config, "BROWSER_EXTENSION_DOWNLOAD_URL", "") or ""
    )
    return status


@router.post("/sessions/{session_id}/tabs")
async def sync_tabs(
    session_id: str,
    body: TabsSyncIn,
    request: Request,
    _: AuthDep = None,
) -> dict[str, Any]:
    user_id = session_user_id(request)
    store = get_session_store()
    session = store.get(session_id)
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Browser session not found")
    session.set_tabs(body.tabs)
    return {"ok": True, "tabs": [t.to_dict() for t in session.authorized_tabs.values()]}


@router.delete("/sessions/{session_id}")
async def close_browser_session(
    session_id: str,
    request: Request,
    _: AuthDep = None,
) -> dict[str, Any]:
    user_id = session_user_id(request)
    store = get_session_store()
    session = store.get(session_id)
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Browser session not found")
    gw = get_gateway()
    gw.unregister_link(session_id)
    store.drop(session_id)
    return {"ok": True}


@router.websocket("/ws")
async def browser_ws(websocket: WebSocket) -> None:
    """Authenticated WebSocket for browser tool dispatch.

    Query: ``session_id=brs_…``
    Auth: session cookie (Starlette SessionMiddleware) or prior API key is not
    available on WS easily — use cookie session only for V1.
    """
    await websocket.accept()
    session_id = (websocket.query_params.get("session_id") or "").strip()
    if not session_id:
        await websocket.send_json({"type": "error", "message": "session_id required"})
        await websocket.close(code=4400)
        return

    # Cookie session auth.
    user_id = "web"
    try:
        # Starlette session is populated by SessionMiddleware for WS too.
        sess = websocket.scope.get("session") or {}
        if sess.get("auth") or sess.get("user_id"):
            user_id = str(sess.get("user_id") or "web")
        elif not sess.get("auth"):
            # Allow unauthenticated local single-user installs that use default.
            user_id = str(sess.get("user_id") or "web")
    except Exception:
        user_id = "web"

    store = get_session_store()
    session = store.get(session_id)
    if session is None:
        await websocket.send_json({"type": "error", "message": "unknown session"})
        await websocket.close(code=4404)
        return
    if session.user_id != user_id and session.user_id not in {"web", user_id}:
        # Strict ownership: session must belong to authenticated user.
        # When both are "web" (default local), allow.
        if not (session.user_id == "web" and user_id == "web"):
            await websocket.send_json({"type": "error", "message": "forbidden"})
            await websocket.close(code=4403)
            return

    loop = asyncio.get_running_loop()
    link = BrowserClientLink(session, websocket, loop)
    gw = get_gateway()
    prev = gw.register_link(link)
    if prev is not None and prev is not link:
        try:
            await prev.websocket.close(code=4000)
        except Exception:
            pass

    # Hello ack
    await websocket.send_json(
        envelope(
            "browser.hello",
            session_id=session_id,
            payload={
                "status": "connected",
                "capabilities": sorted(session.capabilities),
            },
        )
    )

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                break
            if not isinstance(raw, dict):
                continue
            msg_type = str(raw.get("type") or "")
            if msg_type == TYPE_HEARTBEAT:
                link.touch()
                await websocket.send_json(
                    envelope(
                        TYPE_HEARTBEAT,
                        session_id=session_id,
                        payload={"ok": True},
                    )
                )
                continue
            gw.handle_client_message(session_id, raw)
    finally:
        gw.unregister_link(session_id, websocket=websocket)
        logger.info("browser ws closed session=%s user=%s", session_id, user_id)
