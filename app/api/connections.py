"""Generic secure-input broker plus the existing HTTP consumer endpoints."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.core.deps import AuthDep, require_owned_session, session_user_id
from app.services import connections, secret_files, secret_store, store
from app.runtime.access import AccessDenied

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
            "Broker access missing or expired; run from a local or updated tunnel Tomo chat bash tool",
        )
    # A bearer capability is not a platform elevation or a stale account lease.
    try:
        user = store.access.require_user(scope.get("user_id") or "")
        sid = scope.get("session_id") or ""
        context = store.access.resolve_context(user["id"], sid)
        store.access.revalidate(context)
    except AccessDenied:
        raise HTTPException(403, "Broker execution identity unavailable") from None
    if user["role"] != "admin":
        # Current consumers write host files and use privileged HTTP networking.
        # They cannot satisfy a restricted Member's containment/network ceiling.
        raise HTTPException(503, "Isolation-aware credential consumer unavailable")
    return {**scope, "token": token}


BrokerScope = Annotated[dict, Depends(broker_scope)]


def _broker_execution(scope: dict, *, bundle_name: str | None = None, write: bool = False):
    from app.runtime.access import AccessDenied
    context = store.access.resolve_context(scope["user_id"], scope["session_id"])
    wid = scope.get("workplace_id")
    if wid:
        # Tunnel-scoped consumer (scoped credential for a remote
        # destination). Session-scoping comes first: another session's
        # bundle is 404, never a mode/destination signal. Then the scope
        # session must enable the destination (with write permission for
        # credential-merged file writes), and the destination must be a
        # verified, currently-online contract enforcer.
        if bundle_name is not None:
            try:
                secret_store.scoped_bundle(scope, bundle_name)
            except (KeyError, ValueError):
                raise HTTPException(404, "Secret bundle not found in this session") from None
        wp = store.get_workplace(wid)
        if not wp or wp.get("kind") != "tunnel":
            raise HTTPException(503, "Credential consumer destination is unavailable") from None
        try:
            store.access.authorize_resource(context, wid, write=write)
        except AccessDenied:
            raise HTTPException(503, "Credential consumer destination is outside the execution scope") from None
        if context.execution_mode != "unrestricted":
            # Stage 1 boundary preserved: credential-merged writes and
            # credential-bearing network use need the explicit unrestricted
            # destination grant + acknowledgement, never sandbox containment.
            raise HTTPException(503, "Credential consumer requires explicitly unrestricted destination execution") from None
        from app.workplaces import remote_contract
        try:
            remote_contract.require_tunnel_destination(wp, mode="unrestricted")
        except AccessDenied as exc:
            raise HTTPException(503, str(exc)) from None
        return context
    if (context.role != "admin" or context.execution_mode != "unrestricted"
            or any(r.kind != "local" for r in context.resources)):
        raise HTTPException(503, "Credential consumer requires explicitly unrestricted local Admin execution")
    return context


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


@router.post("/secret-broker/apply")
async def broker_apply_file(request: Request, scope: BrokerScope):
    data = await _body(request, 65_536)
    bundle = data.get("bundle") if isinstance(data, dict) else None
    context = _broker_execution(scope, bundle_name=bundle if isinstance(bundle, str) else None, write=True)
    from app.runtime.access import execution_scope
    from app.runtime.supervision import admitted_turn
    def apply():
        with store.access.execution_guard(context):
            store.access.audit(context.user_id, "broker.file", session_id=context.session_id,
                               agent_id=context.agent_id, destination_id=context.destination_id, outcome="admitted")
            return secret_files.apply_file(scope, data)
    try:
        with execution_scope(context):
            async with admitted_turn(context):
                return await run_in_threadpool(apply)
    except KeyError:
        raise HTTPException(404, "Secret bundle not found in this session") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/connection-broker/http")
async def broker_http(request: Request, scope: BrokerScope):
    context = _broker_execution(scope)
    data = await _body(request)
    from app.runtime.access import execution_scope
    from app.runtime.supervision import admitted_turn
    try:
        with execution_scope(context):
            async with admitted_turn(context):
                store.access.audit(context.user_id, "broker.http", session_id=context.session_id,
                                   agent_id=context.agent_id, destination_id=context.destination_id, outcome="admitted")
                return await connections.execute_http(scope, data)
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
