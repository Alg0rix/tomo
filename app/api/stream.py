"""Realtime SSE endpoints — chat stream and agent state."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from pydantic import BaseModel, Field

from app.core.deps import AuthDep, session_user_id
from app.services import (
    heartbeat_stream,
    run_turn,
    session_heartbeat_stream,
    store,
)
from app.services.chat import get_active_session_turn, start_session_turn


class SessionChatStreamIn(BaseModel):
    message: str = ""
    attachment_ids: list[str] = Field(default_factory=list)


router = APIRouter(prefix="/api")

_HEARTBEAT_S = 8.0


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
    """Start (or join) a session turn; stream Tomo SSE. Prefer this over GET."""
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    message = (body.message or "").strip()
    attachment_ids = list(body.attachment_ids or [])
    if not message and not attachment_ids:
        raise HTTPException(status_code=400, detail="Message is required")
    uid = session_user_id(request)

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"
        active = get_active_session_turn(session_id)
        if active:
            queue = active.subscribe(after_seq=0)
        else:
            try:
                active, queue = await start_session_turn(
                    session_id,
                    message,
                    uid,
                    start_seq=0,
                    attachment_ids=attachment_ids,
                )
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
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"

        # Listen mode.  If there's an active turn, subscribe
        # to it (reconnect after refresh) with replay of missed events.
        # Otherwise, heartbeat stream.
        active = get_active_session_turn(session_id)
        if active:
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
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    message = (body.message or "").strip()
    attachment_ids = list(body.attachment_ids or [])
    if not message and not attachment_ids:
        raise HTTPException(status_code=400, detail="Message is required")
    uid = session_user_id(request)

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"
        async with contextlib.aclosing(
            run_turn(
                agent_id,
                message,
                uid,
                start_seq=0,
                attachment_ids=attachment_ids,
            )
        ) as agen:
            async for chunk in agen:
                if await request.is_disconnected():
                    return
                yield chunk
        yield fmt_sse(
            {
                "event": "turn.end",
                "data": {"agent_id": agent_id, "ok": True},
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


@router.get("/agents/{agent_id}/chat/stream")
async def chat_stream(
    agent_id: str,
    request: Request,
    message: str = "",
    user_id: str = "web",
    attachment_ids: Annotated[list[str], Query()] = [],
    _: AuthDep = None,
):
    """Heartbeat / idle listen only. Starting a turn requires POST."""
    if (message or "").strip() or attachment_ids:
        raise HTTPException(
            status_code=405,
            detail="Use POST /api/agents/{id}/chat/stream to start a turn",
        )
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"
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
