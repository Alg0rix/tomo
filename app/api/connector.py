"""Tomo Connector WebSocket endpoint (pairing + RPC hub).

Path: ``/api/connector/ws``

Protocol (JSON, ``v: 1``):

* Client ``pair`` {code, hostname?, version?} → server ``pair_ok`` {workplace_id, token}
* Client ``hello`` {token, hostname?, version?} → server ``hello_ok`` {workplace_id}
* Client ``heartbeat`` → server ``heartbeat_ack``
* Server ``rpc_request`` {id, method, params} → client ``rpc_response`` {id, ok, result|error}
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services import store
from app.workplaces.hub import ConnectorSession, hub
from app.workplaces.pairing import rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter()

_PROTOCOL_V = 1


def _err(message: str) -> dict[str, Any]:
    return {"v": _PROTOCOL_V, "type": "error", "message": message}


@router.websocket("/api/connector/ws")
async def connector_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    workplace_id: str | None = None
    session: ConnectorSession | None = None
    client_host = ""
    if websocket.client:
        client_host = websocket.client.host or ""

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                await websocket.send_json(_err("invalid JSON message"))
                continue

            if not isinstance(raw, dict):
                await websocket.send_json(_err("message must be a JSON object"))
                continue

            msg_type = str(raw.get("type") or "").strip().lower()
            if msg_type == "pair":
                if not rate_limiter.allow(f"pair:{client_host or 'unknown'}"):
                    await websocket.send_json(_err("too many pairing attempts"))
                    await websocket.close(code=4408)
                    return
                code = str(raw.get("code") or "").strip()
                hostname = str(raw.get("hostname") or "").strip()
                version = str(raw.get("version") or "").strip()
                try:
                    result = store.pair_connector(
                        code, hostname=hostname, version=version
                    )
                except ValueError as exc:
                    await websocket.send_json(_err(str(exc)))
                    continue
                if not result:
                    await websocket.send_json(_err("invalid or expired pairing code"))
                    continue
                workplace_id = result["workplace_id"]
                session = await _bind_session(
                    websocket, workplace_id, hostname=hostname, version=version
                )
                await websocket.send_json(
                    {
                        "v": _PROTOCOL_V,
                        "type": "pair_ok",
                        "workplace_id": workplace_id,
                        "token": result["token"],
                    }
                )
                continue

            if msg_type == "hello":
                if not rate_limiter.allow(f"hello:{client_host or 'unknown'}"):
                    await websocket.send_json(_err("too many auth attempts"))
                    await websocket.close(code=4408)
                    return
                token = str(raw.get("token") or "").strip()
                hostname = str(raw.get("hostname") or "").strip()
                version = str(raw.get("version") or "").strip()
                try:
                    result = store.hello_connector(
                        token, hostname=hostname, version=version
                    )
                except ValueError as exc:
                    await websocket.send_json(_err(str(exc)))
                    continue
                if not result:
                    await websocket.send_json(_err("invalid connector token"))
                    await websocket.close(code=4401)
                    return
                workplace_id = result["workplace_id"]
                session = await _bind_session(
                    websocket, workplace_id, hostname=hostname, version=version
                )
                await websocket.send_json(
                    {
                        "v": _PROTOCOL_V,
                        "type": "hello_ok",
                        "workplace_id": workplace_id,
                    }
                )
                continue

            if session is None or workplace_id is None:
                await websocket.send_json(
                    _err("authenticate first with pair or hello")
                )
                continue

            if msg_type == "heartbeat":
                session.touch()
                store.touch_connector(workplace_id)
                await websocket.send_json(
                    {"v": _PROTOCOL_V, "type": "heartbeat_ack"}
                )
                continue

            if msg_type == "rpc_response":
                req_id = str(raw.get("id") or "")
                if not req_id:
                    continue
                session.touch()
                payload = {
                    "ok": bool(raw.get("ok")),
                    "result": raw.get("result"),
                    "error": raw.get("error"),
                }
                session.resolve_rpc(req_id, payload)
                continue

            await websocket.send_json(_err(f"unknown message type: {msg_type}"))

    finally:
        if workplace_id is not None:
            if hub.unregister(workplace_id, websocket):
                try:
                    store.mark_connector_offline(workplace_id)
                except Exception:  # pragma: no cover - shutdown edge
                    logger.exception("mark offline failed for %s", workplace_id)


async def _bind_session(
    websocket: WebSocket,
    workplace_id: str,
    *,
    hostname: str,
    version: str,
) -> ConnectorSession:
    loop = asyncio.get_running_loop()
    session = ConnectorSession(
        workplace_id,
        websocket,
        loop,
        hostname=hostname,
        version=version,
    )
    prev = hub.register(session)
    if prev is not None and prev.websocket is not websocket:
        prev.fail_all("replaced by new connector session")
        try:
            await prev.websocket.close(code=4000)
        except Exception:
            pass
    return session


__all__ = ["router"]
