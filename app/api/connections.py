"""Generic secure-input broker plus the existing HTTP consumer endpoints."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.deps import AuthDep, require_owned_session, session_user_id
from app.services import connections, secret_store

router = APIRouter(prefix="/api", tags=["secrets"])


async def _body(request: Request, limit: int = 1_050_000) -> dict:
    # Manual validation avoids credential values in Pydantic error responses.
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HTTPException(413, "Request too large")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, "Expected a JSON object") from None


def broker_scope(request: Request) -> dict:
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
    scope = secret_store.capability_scope(token)
    if not scope:
        raise HTTPException(
            401,
            "Broker access missing or expired; run from a local Tomo chat bash tool",
        )
    return {**scope, "token": token}


BrokerScope = Annotated[dict, Depends(broker_scope)]


@router.get("/secret-broker/bundles")
async def broker_bundles(scope: BrokerScope):
    return {"bundles": secret_store.list_bundles(scope["session_id"], scope["user_id"])}


@router.get("/connection-broker/connections")
async def broker_connections(scope: BrokerScope):
    return {
        "connections": connections.list_connections(
            scope["session_id"], scope["user_id"]
        )
    }


@router.delete("/secret-broker/bundles/{name}")
@router.delete("/connection-broker/connections/{name}")
async def broker_revoke(name: str, scope: BrokerScope):
    try:
        row = secret_store.scoped_bundle(scope, name)
    except (KeyError, ValueError):
        raise HTTPException(404, "Secret bundle not found in this session") from None
    if not secret_store.delete_bundle(scope["session_id"], scope["user_id"], row["id"]):
        raise HTTPException(404, "Secret bundle not found in this session")
    return {"ok": True}


def _notify(scope: dict, pending: dict) -> dict:
    from app.services.chat import notify_session_event

    notify_session_event(scope["session_id"], "secret_required", pending)
    return pending


@router.post("/secret-broker/requests")
async def broker_secret_request(request: Request, scope: BrokerScope):
    try:
        pending = secret_store.create_request(
            scope["token"], await _body(request, 65_536)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return _notify(scope, pending)


@router.post("/connection-broker/requests")
async def broker_connection_request(request: Request, scope: BrokerScope):
    try:
        pending = connections.create_request(
            scope["token"], await _body(request, 65_536)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return _notify(scope, pending)


@router.get("/secret-broker/requests/{request_id}")
@router.get("/connection-broker/requests/{request_id}")
async def broker_status(request_id: str, scope: BrokerScope):
    result = secret_store.request_status(request_id, scope["token"])
    if result is None:
        raise HTTPException(404, "Secure-input request expired or unavailable")
    if result.get("bundle") and result["bundle"]["usage"].get("type") == "http":
        result["connection"] = connections.public_connection(result["bundle"])
    return result


@router.post("/connection-broker/http")
async def broker_http(request: Request, scope: BrokerScope):
    try:
        return await connections.execute_http(scope, await _body(request))
    except KeyError:
        raise HTTPException(404, "Secret bundle not found in this session") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/sessions/{session_id}/secrets/requests/{request_id}")
@router.post("/sessions/{session_id}/connections/requests/{request_id}")
async def submit_secret(session_id: str, request_id: str, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
    try:
        # The HTTP consumer validates its usage when present; store-only
        # requests have no origin, auth strategy, or execution requirements.
        return connections.resolve_request(
            request_id,
            session_id,
            session_user_id(request),
            await _body(request, 524_288),
        )
    except KeyError:
        raise HTTPException(
            404, "Secure-input request expired or unavailable"
        ) from None
    except RuntimeError:
        raise HTTPException(409, "Secure-input request already resolved") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/sessions/{session_id}/secrets")
async def browser_bundles(session_id: str, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
    return {"bundles": secret_store.list_bundles(session_id, session_user_id(request))}


@router.get("/sessions/{session_id}/connections")
async def browser_connections(session_id: str, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
    return {
        "connections": connections.list_connections(
            session_id, session_user_id(request)
        )
    }


@router.delete("/sessions/{session_id}/secrets/{bundle_id}")
@router.delete("/sessions/{session_id}/connections/{bundle_id}")
async def browser_delete(session_id: str, bundle_id: str, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
    if not secret_store.delete_bundle(session_id, session_user_id(request), bundle_id):
        raise HTTPException(404, "Secret bundle not found")
    return {"ok": True}
