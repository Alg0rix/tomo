"""Realtime SSE endpoints — chat stream and agent state."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Annotated, Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from pydantic import BaseModel, Field

from app.core.deps import AuthDep, require_owned_session, session_user_id
from app.runtime.ui import UIValidationError, validate_ui_action
from app.services import (
    heartbeat_stream,
    session_heartbeat_stream,
    store,
)
from app.channels.sse_map import session_busy_sse
from app.services.chat import (
    SessionTurnBusy,
    cancel_session_turn,
    get_active_session_turn,
    push_session_steer,
    start_session_turn,
)


class SessionChatStreamIn(BaseModel):
    message: str = ""
    attachment_ids: list[str] = Field(default_factory=list)


class SessionUIActionIn(BaseModel):
    ui_id: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


router = APIRouter(prefix="/api")

_HEARTBEAT_S = 8.0


def _resume_chrome_sse(session_id: str, *, agent_id: str = "") -> list[str]:
    """SSE chunks that restore HITL cards + todo dock after refresh/reconnect.

    Pending approvals/clarifies and the in-memory todo list live outside the
    turn ring buffer, so a pure event replay can miss them. Emit a snapshot
    before replaying buffered turn events.
    """
    from app.channels.sse_map import fmt_sse

    chunks: list[str] = []
    try:
        from app.runtime.permissions import hitl

        pending = hitl.list_pending_for_session(session_id)
        for payload in pending.get("approvals") or []:
            data = dict(payload)
            if agent_id and not data.get("agent_id"):
                data["agent_id"] = agent_id
            chunks.append(
                fmt_sse({"event": "approval_required", "data": data, "seq": 0})
            )
        for payload in pending.get("clarifies") or []:
            data = dict(payload)
            if agent_id and not data.get("agent_id"):
                data["agent_id"] = agent_id
            chunks.append(
                fmt_sse({"event": "clarify_required", "data": data, "seq": 0})
            )
    except Exception:
        pass
    try:
        from app.runtime.tools import todo as todo_mod

        todos = list(todo_mod.get_store(session_id).snapshot().get("todos") or [])
        if todos:
            chunks.append(
                fmt_sse(
                    {
                        "event": "todos",
                        "data": {
                            "todos": todos,
                            "source": "resume",
                            "agent_id": agent_id,
                            "session_id": session_id,
                        },
                        "seq": 0,
                    }
                )
            )
    except Exception:
        pass
    return chunks


async def _drain_queue_with_heartbeats(
    queue: asyncio.Queue,
    request: Request,
    *,
    on_exit=None,
) -> AsyncIterator[str]:
    """Read chunks from *queue*, yielding heartbeats on timeout.

    Returns when the producer signals ``None`` (turn complete) or the
    client disconnects.  Does NOT cancel the producer — the background
    turn continues even after the client leaves.
    """
    from app.channels.sse_map import fmt_sse

    try:
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_S)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    return
                yield fmt_sse({"event": "heartbeat", "data": {}})
                continue
            if chunk is None:
                return
            if await request.is_disconnected():
                return
            yield chunk
    finally:
        if on_exit is not None:
            try:
                on_exit()
            except Exception:
                pass


@router.post("/sessions/{session_id}/chat/stream")
async def session_chat_stream_post(
    session_id: str,
    body: SessionChatStreamIn,
    request: Request,
    _: AuthDep = None,
):
    """Start a session turn and stream Tomo SSE. Prefer this over GET.

    If a turn is already running, emit ``session_busy`` so the client can
    re-queue. Reconnect/watch uses GET listen — never drop a new message by
    joining an in-flight turn.
    """
    session = require_owned_session(request, session_id)
    message = (body.message or "").strip()
    attachment_ids = list(body.attachment_ids or [])
    if not message and not attachment_ids:
        raise HTTPException(status_code=400, detail="Message is required")
    uid = session_user_id(request)
    coordinator_id = session.get("coordinator_id") or session.get("agent_id") or ""

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"
        busy = session_busy_sse(
            agent_id=coordinator_id, session_id=session_id, seq=1
        )
        if get_active_session_turn(session_id) is not None:
            yield busy
            return
        try:
            active, queue = await start_session_turn(
                session_id,
                message,
                uid,
                start_seq=0,
                attachment_ids=attachment_ids,
            )
        except SessionTurnBusy:
            yield busy
            return
        except ValueError:
            yield fmt_sse(
                {
                    "event": "error",
                    "data": {"message": "Could not start turn"},
                }
            )
            return

        owner = active

        def _release() -> None:
            if owner is not None:
                owner.unsubscribe(queue)

        async for chunk in _drain_queue_with_heartbeats(
            queue, request, on_exit=_release
        ):
            yield chunk

        yield fmt_sse(
            {
                "event": "turn.end",
                "data": {"session_id": session_id, "ok": True},
                "seq": 9999,
            }
        )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/sessions/{session_id}/chat/stop")
async def session_chat_stop(session_id: str, request: Request, _: AuthDep = None):
    """Cancel the in-flight background turn for this session (if any)."""
    require_owned_session(request, session_id)
    stopped = cancel_session_turn(session_id)
    return {"ok": True, "stopped": stopped, "session_id": session_id}


@router.post("/sessions/{session_id}/chat/steer")
async def session_chat_steer(
    session_id: str,
    body: SessionChatStreamIn,
    request: Request,
    _: AuthDep = None,
):
    """Inject a user message into the running turn (mid-turn steer).

    Used when the composer already has queued follow-ups and the user presses
    Enter again — merge client-side, then POST here. Does not start a new turn.
    """
    require_owned_session(request, session_id)
    from app.services.chat import push_session_steer

    message = (body.message or "").strip()
    attachment_ids = list(body.attachment_ids or [])
    result = push_session_steer(session_id, message, attachment_ids=attachment_ids)
    if not result.get("accepted"):
        reason = result.get("reason") or "rejected"
        status = 409 if reason == "no_active_turn" else 400
        raise HTTPException(status_code=status, detail=reason)
    return result


@router.post("/sessions/{session_id}/ui-actions")
async def session_ui_action(
    session_id: str,
    body: SessionUIActionIn,
    request: Request,
    _: AuthDep = None,
):
    """Dispatch a typed generative-UI action into the session agent turn."""
    require_owned_session(request, session_id)
    try:
        action = validate_ui_action(body.model_dump())
    except UIValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Keep the model-facing message structured and bounded. The frontend can
    # reconnect to the existing stream when an idle action starts a new turn.
    message = "[UI action]\n" + json.dumps(
        action, ensure_ascii=False, separators=(",", ":")
    )
    user_id = session_user_id(request)
    active = get_active_session_turn(session_id)
    if active is not None:
        result = push_session_steer(session_id, message)
        if result.get("accepted"):
            return {**result, "mode": "steer", "ui_id": action["ui_id"]}
        raise HTTPException(status_code=409, detail=result.get("reason") or "session busy")

    try:
        active, queue = await start_session_turn(session_id, message, user_id, start_seq=0)
        # The action request only kicks off the background turn; the browser
        # reconnects through the normal listen SSE endpoint. Do not leave the
        # starter subscription queued without a consumer.
        active.unsubscribe(queue)
    except SessionTurnBusy:
        result = push_session_steer(session_id, message)
        if result.get("accepted"):
            return {**result, "mode": "steer", "ui_id": action["ui_id"]}
        raise HTTPException(status_code=409, detail="Session is busy") from None
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "ok": True,
        "accepted": True,
        "mode": "started",
        "session_id": session_id,
        "ui_id": action["ui_id"],
    }


@router.get("/sessions/{session_id}/chat/stream")
async def session_chat_stream(
    session_id: str,
    request: Request,
    message: str = "",
    user_id: str = "web",
    after: int = 0,
    attachment_ids: Annotated[list[str], Query()] = [],
    _: AuthDep = None,
):
    """Listen / resume only. Starting a turn requires POST (CSRF-safe)."""
    if (message or "").strip() or attachment_ids:
        raise HTTPException(
            status_code=405,
            detail="Use POST /api/sessions/{id}/chat/stream to start a turn",
        )
    require_owned_session(request, session_id)

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"

        # Listen mode.  If there's an active turn, subscribe
        # to it (reconnect after refresh) with replay of missed events.
        # Otherwise, heartbeat stream.
        active = get_active_session_turn(session_id)
        if active:
            # Snapshot *before* replay so a quiet mid-turn gap (empty buffer,
            # long tool wait) still marks the client as attached to a live turn.
            session = store.get_owned_session(session_id, session_user_id(request)) or {}
            coord = session.get("coordinator_id") or session.get("agent_id") or ""
            yield fmt_sse(
                {
                    "event": "state",
                    "data": {
                        "agent_id": coord,
                        "busy": True,
                        "session_id": session_id,
                        "resumed": True,
                    },
                    "seq": 0,
                }
            )
            yield fmt_sse(
                {
                    "event": "turn.start",
                    "data": {
                        "session_id": session_id,
                        "agent_id": coord,
                        "resumed": True,
                    },
                    "seq": 0,
                }
            )
            for chunk in _resume_chrome_sse(session_id, agent_id=coord):
                yield chunk
            queue = active.subscribe(after_seq=after)
            owner = active

            def _release_listen() -> None:
                owner.unsubscribe(queue)

            async for chunk in _drain_queue_with_heartbeats(
                queue, request, on_exit=_release_listen
            ):
                yield chunk
            yield fmt_sse(
                {
                    "event": "turn.end",
                    "data": {"session_id": session_id, "ok": True},
                    "seq": 9999,
                }
            )
            return

        async with contextlib.aclosing(
            session_heartbeat_stream(session_id, start_seq=1000)
        ) as agen:
            async for chunk in agen:
                if await request.is_disconnected():
                    return
                yield chunk

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class AgentChatStreamIn(BaseModel):
    message: str = ""
    attachment_ids: list[str] = Field(default_factory=list)


@router.post("/agents/{agent_id}/chat/stream")
async def chat_stream_post(
    agent_id: str,
    body: AgentChatStreamIn,
    request: Request,
    _: AuthDep = None,
):
    """Start a background turn on the agent's solo session (same as session POST)."""
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    message = (body.message or "").strip()
    attachment_ids = list(body.attachment_ids or [])
    if not message and not attachment_ids:
        raise HTTPException(status_code=400, detail="Message is required")
    uid = session_user_id(request)
    try:
        session_id = store.get_or_create_session(agent_id, uid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"
        busy = session_busy_sse(agent_id=agent_id, session_id=session_id, seq=1)
        if get_active_session_turn(session_id) is not None:
            yield busy
            return
        try:
            active, queue = await start_session_turn(
                session_id,
                message,
                uid,
                start_seq=0,
                attachment_ids=attachment_ids,
            )
        except SessionTurnBusy:
            yield busy
            return
        except ValueError:
            yield fmt_sse(
                {"event": "error", "data": {"message": "Could not start turn"}}
            )
            return

        def _release() -> None:
            active.unsubscribe(queue)

        async for chunk in _drain_queue_with_heartbeats(
            queue, request, on_exit=_release
        ):
            yield chunk
        yield fmt_sse(
            {
                "event": "turn.end",
                "data": {"agent_id": agent_id, "session_id": session_id, "ok": True},
                "seq": 9999,
            }
        )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/agents/{agent_id}/chat/stop")
async def agent_chat_stop(
    agent_id: str,
    request: Request,
    _: AuthDep = None,
):
    """Cancel the in-flight turn on this agent's solo session (if any)."""
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    uid = session_user_id(request)
    session_id = store.find_session(agent_id, uid)
    if not session_id:
        return {"ok": True, "stopped": False, "session_id": None}
    stopped = cancel_session_turn(session_id)
    return {"ok": True, "stopped": stopped, "session_id": session_id}


@router.get("/agents/{agent_id}/chat/stream")
async def chat_stream(
    agent_id: str,
    request: Request,
    message: str = "",
    user_id: str = "web",
    attachment_ids: Annotated[list[str], Query()] = [],
    _: AuthDep = None,
):
    """Listen/resume the agent's solo session turn, else idle heartbeats."""
    if (message or "").strip() or attachment_ids:
        raise HTTPException(
            status_code=405,
            detail="Use POST /api/agents/{id}/chat/stream to start a turn",
        )
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    uid = session_user_id(request)
    session_id = store.find_session(agent_id, uid)

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"
        if session_id:
            active = get_active_session_turn(session_id)
            if active:
                # Same resume snapshot as session listen — keeps UI attached
                # through long quiet gaps after refresh.
                yield fmt_sse(
                    {
                        "event": "state",
                        "data": {
                            "agent_id": agent_id,
                            "busy": True,
                            "session_id": session_id,
                            "resumed": True,
                        },
                        "seq": 0,
                    }
                )
                yield fmt_sse(
                    {
                        "event": "turn.start",
                        "data": {
                            "agent_id": agent_id,
                            "session_id": session_id,
                            "resumed": True,
                        },
                        "seq": 0,
                    }
                )
                for chunk in _resume_chrome_sse(session_id, agent_id=agent_id):
                    yield chunk
                queue = active.subscribe(after_seq=0)

                def _release() -> None:
                    active.unsubscribe(queue)

                async for chunk in _drain_queue_with_heartbeats(
                    queue, request, on_exit=_release
                ):
                    yield chunk
                yield fmt_sse(
                    {
                        "event": "turn.end",
                        "data": {
                            "agent_id": agent_id,
                            "session_id": session_id,
                            "ok": True,
                        },
                        "seq": 9999,
                    }
                )
                return

        async with contextlib.aclosing(
            heartbeat_stream(agent_id, start_seq=1000)
        ) as agen:
            async for chunk in agen:
                if await request.is_disconnected():
                    return
                yield chunk

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/agents/{agent_id}/state")
async def agent_state(
    agent_id: str,
    _: AuthDep,
    session_id: str | None = None,
):
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    busy = bool(session_id) and store.is_agent_busy(agent_id, session_id)
    return {
        "agent_id": agent_id,
        "busy": busy,
        "enabled": agent["enabled"],
        "session_id": session_id or "",
    }
