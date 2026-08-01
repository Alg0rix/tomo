"""Realtime SSE endpoints — chat stream and agent state."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.deps import AuthDep, session_user_id
from app.services import heartbeat_stream, run_turn, session_heartbeat_stream, store
from app.services.chat import get_active_session_turn, start_session_turn

router = APIRouter(prefix="/api")

_HEARTBEAT_S = 8.0


def _resolve_user_id(request: Request, user_id: str | None) -> str:
    uid = (user_id or "").strip()
    if uid and uid != "web":
        return uid
    return session_user_id(request)


async def _drain_queue_with_heartbeats(
    queue: asyncio.Queue, request: Request
) -> AsyncIterator[str]:
    """Read chunks from *queue*, yielding heartbeats on timeout.

    Returns when the producer signals ``None`` (turn complete) or the
    client disconnects.  Does NOT cancel the producer — the background
    turn continues even after the client leaves.
    """
    from app.channels.sse_map import fmt_sse

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
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    message = (message or "").strip()
    uid = _resolve_user_id(request, user_id)
    attachment_ids = list(attachment_ids or [])

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"

        if message or attachment_ids:
            # Start a new background turn (or join if one is already active).
            active = get_active_session_turn(session_id)
            if active:
                queue = active.subscribe(after_seq=after)
            else:
                try:
                    queue = await start_session_turn(
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

            async for chunk in _drain_queue_with_heartbeats(queue, request):
                yield chunk

            yield fmt_sse(
                {
                    "event": "turn.end",
                    "data": {"session_id": session_id, "ok": True},
                    "seq": 9999,
                }
            )
            return

        # No message — listen mode.  If there's an active turn, subscribe
        # to it (reconnect after refresh) with replay of missed events.
        # Otherwise, heartbeat stream.
        active = get_active_session_turn(session_id)
        if active:
            queue = active.subscribe(after_seq=after)
            async for chunk in _drain_queue_with_heartbeats(queue, request):
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


@router.get("/agents/{agent_id}/chat/stream")
async def chat_stream(
    agent_id: str,
    request: Request,
    message: str = "",
    user_id: str = "web",
    attachment_ids: Annotated[list[str], Query()] = [],
    _: AuthDep = None,
):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    message = (message or "").strip()
    uid = _resolve_user_id(request, user_id)
    attachment_ids = list(attachment_ids or [])

    async def event_source():
        from app.channels.sse_map import fmt_sse

        yield "retry: 4000\n\n"
        if message or attachment_ids:
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
