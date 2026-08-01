"""Tomo Connector endpoints (HTTP pair + WebSocket hub).

* ``POST /api/connector/pair`` — unauthenticated HTTP pair (code → token)
* ``WS /api/connector/ws`` — Bearer token in handshake headers preferred;
  JSON ``hello`` / ``pair`` still accepted for older clients

Protocol (JSON, ``v: 1``) after auth:

* ``heartbeat`` ↔ ``heartbeat_ack`` (or ``ping`` ↔ ``pong``)
* server ``rpc_request`` {id, method, params}
* client ``rpc_response`` {id, ok, result|error}
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.services import store
from app.workplaces.hub import ConnectorSession, client_supports_replay, hub
from app.workplaces.pairing import rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter()

_PROTOCOL_V = 1


def _err(message: str) -> dict[str, Any]:
    return {"v": _PROTOCOL_V, "type": "error", "message": message}


def _client_ip(request: Request | WebSocket) -> str:
    if isinstance(request, Request):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host or ""
        return ""
    # WebSocket
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host or ""
    return ""


@router.post("/api/connector/pair")
async def connector_pair_http(request: Request) -> JSONResponse:
    """Pair with a short-lived code (called by tomo-connector; no admin session)."""
    ip = _client_ip(request) or "unknown"
    if not rate_limiter.allow(f"pair:{ip}"):
        return JSONResponse({"ok": False, "error": "too many pairing attempts"}, 429)
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    code = str(
        data.get("pairing_code") or data.get("code") or ""
    ).strip()
    hostname = str(
        data.get("device_name") or data.get("hostname") or ""
    ).strip()
    platform = str(data.get("platform") or "").strip()
    version = str(data.get("version") or "").strip()
    # Prefer device-reported LAN IP over TCP peer (peer is often 127.0.0.1).
    device_ip = str(
        data.get("local_ip") or data.get("device_ip") or ""
    ).strip()
    peer = ip if ip not in ("", "unknown") else ""
    stored_ip = device_ip or (peer if peer not in ("127.0.0.1", "::1") else "")
    if not code:
        return JSONResponse({"ok": False, "error": "pairing_code is required"}, 400)
    try:
        result = store.pair_connector(
            code,
            hostname=hostname,
            version=version,
            platform=platform,
            remote_ip=stored_ip,
        )
    except ValueError:
        return JSONResponse(
            {"ok": False, "error": "Invalid or expired pairing code"}, 400
        )
    if not result:
        return JSONResponse(
            {"ok": False, "error": "Invalid or expired pairing code"}, 400
        )
    return JSONResponse(
        {
            "ok": True,
            "connector_token": result["token"],
            "workplace_id": result["workplace_id"],
            "workplace_name": result.get("workplace_name") or result["workplace_id"],
        }
    )


@router.websocket("/api/connector/ws")
async def connector_ws(websocket: WebSocket) -> None:
    client_host = _client_ip(websocket)
    # Prefer Bearer auth at handshake.
    auth = websocket.headers.get("authorization") or ""
    token_hdr = ""
    if auth.lower().startswith("bearer "):
        token_hdr = auth[7:].strip()
    hostname = (
        websocket.headers.get("x-device-name")
        or websocket.headers.get("x-tomo-device")
        or ""
    ).strip()
    platform = (
        websocket.headers.get("x-platform")
        or websocket.headers.get("x-tomo-platform")
        or ""
    ).strip()
    version = (
        websocket.headers.get("x-tomo-connector-version")
        or ""
    ).strip()
    caps = (
        websocket.headers.get("x-tomo-caps")
        or ""
    ).strip()
    device_ip = (
        websocket.headers.get("x-tomo-local-ip")
        or websocket.headers.get("x-device-ip")
        or ""
    ).strip()
    peer = client_host or ""
    # Device LAN IP preferred; never treat loopback peer as "the" device IP.
    stored_ip = device_ip or (peer if peer not in ("127.0.0.1", "::1", "unknown") else "")

    workplace_id: str | None = None
    session: ConnectorSession | None = None

    if token_hdr:
        if not rate_limiter.allow(f"hello:{client_host or 'unknown'}"):
            await websocket.close(code=4408)
            return
        try:
            result = store.hello_connector(
                token_hdr,
                hostname=hostname,
                version=version,
                platform=platform,
                remote_ip=stored_ip,
            )
        except ValueError:
            result = None
        if not result:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        workplace_id = result["workplace_id"]
        session = await _bind_session(
            websocket,
            workplace_id,
            hostname=hostname,
            version=version,
            platform=platform,
            remote_ip=stored_ip,
            caps=caps,
        )
        try:
            await websocket.send_json(
                {
                    "v": _PROTOCOL_V,
                    "type": "hello_ok",
                    "workplace_id": workplace_id,
                }
            )
        except Exception:
            pass
    else:
        await websocket.accept()

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                try:
                    await websocket.send_json(_err("invalid JSON message"))
                except Exception:
                    break
                continue

            if not isinstance(raw, dict):
                await websocket.send_json(_err("message must be a JSON object"))
                continue

            msg_type = str(raw.get("type") or "").strip().lower()

            # Application-level ping.
            if msg_type == "ping":
                if session is not None:
                    session.touch()
                await websocket.send_json({"v": _PROTOCOL_V, "type": "pong"})
                continue

            if msg_type == "pair":
                if not rate_limiter.allow(f"pair:{client_host or 'unknown'}"):
                    await websocket.send_json(_err("too many pairing attempts"))
                    await websocket.close(code=4408)
                    return
                code = str(raw.get("code") or raw.get("pairing_code") or "").strip()
                hostname = str(raw.get("hostname") or raw.get("device_name") or "").strip()
                version = str(raw.get("version") or "").strip()
                platform = str(raw.get("platform") or "").strip()
                msg_ip = str(raw.get("local_ip") or raw.get("device_ip") or "").strip()
                use_ip = msg_ip or stored_ip
                try:
                    result = store.pair_connector(
                        code,
                        hostname=hostname,
                        version=version,
                        platform=platform,
                        remote_ip=use_ip,
                    )
                except ValueError:
                    await websocket.send_json(_err("invalid pairing request"))
                    continue
                if not result:
                    await websocket.send_json(_err("invalid or expired pairing code"))
                    continue
                workplace_id = result["workplace_id"]
                session = await _bind_session(
                    websocket,
                    workplace_id,
                    hostname=hostname,
                    version=version,
                    platform=platform,
                    remote_ip=use_ip,
                    caps=str(raw.get("caps") or ""),
                )
                await websocket.send_json(
                    {
                        "v": _PROTOCOL_V,
                        "type": "pair_ok",
                        "workplace_id": workplace_id,
                        "token": result["token"],
                        "connector_token": result["token"],
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
                platform = str(raw.get("platform") or "").strip()
                msg_ip = str(raw.get("local_ip") or raw.get("device_ip") or "").strip()
                use_ip = msg_ip or stored_ip
                try:
                    result = store.hello_connector(
                        token,
                        hostname=hostname,
                        version=version,
                        platform=platform,
                        remote_ip=use_ip,
                    )
                except ValueError:
                    await websocket.send_json(_err("invalid hello request"))
                    continue
                if not result:
                    await websocket.send_json(_err("invalid connector token"))
                    await websocket.close(code=4401)
                    return
                workplace_id = result["workplace_id"]
                session = await _bind_session(
                    websocket,
                    workplace_id,
                    hostname=hostname,
                    version=version,
                    platform=platform,
                    remote_ip=use_ip,
                    caps=str(raw.get("caps") or ""),
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
                    _err("authenticate first (Bearer token, pair, or hello)")
                )
                continue

            if msg_type in ("heartbeat", "pong"):
                session.touch()
                store.touch_connector(
                    workplace_id,
                    remote_ip=session.remote_ip or stored_ip,
                    hostname=session.hostname,
                    version=session.version,
                    platform=session.platform,
                )
                if msg_type == "heartbeat":
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
            # Stale-connection guard: only unregister if this socket is current.
            if hub.unregister(workplace_id, websocket):
                try:
                    store.mark_connector_offline(workplace_id)
                except Exception:  # pragma: no cover
                    logger.exception("mark offline failed for %s", workplace_id)


async def _bind_session(
    websocket: WebSocket,
    workplace_id: str,
    *,
    hostname: str,
    version: str,
    platform: str = "",
    remote_ip: str = "",
    caps: str = "",
) -> ConnectorSession:
    loop = asyncio.get_running_loop()
    replay_ok = client_supports_replay(caps=caps, version=version)
    session = ConnectorSession(
        workplace_id,
        websocket,
        loop,
        hostname=hostname,
        version=version,
        platform=platform,
        remote_ip=remote_ip,
        replay_ok=replay_ok,
    )
    prev = hub.register(session)
    if prev is not None and prev.websocket is not websocket:
        # Hand off in-flight RPCs when both sides support replay.
        if replay_ok and prev.replay_ok:
            pending = prev.take_pending_for_replay()
            if pending:
                session.adopt_pending(pending)
        else:
            prev.fail_all("replaced by new connector session")
        try:
            await prev.websocket.close(code=4000)
        except Exception:
            pass
    return session


__all__ = ["router"]
