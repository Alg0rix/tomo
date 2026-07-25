"""Realtime SSE endpoints — chat stream and agent state."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.deps import AuthDep
from app.services import heartbeat_stream, run_session_turn, run_turn, session_heartbeat_stream, store

router = APIRouter(prefix="/api")


@router.get("/sessions/{session_id}/chat/stream")
async def session_chat_stream(
    session_id: str,
    request: Request,
    message: str = "",
    user_id: str = "web",
    _: AuthDep = None,
):
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    message = (message or "").strip()

    async def event_source():
        yield "retry: 4000\n\n"
        if message:
            async for chunk in run_session_turn(session_id, message, user_id, start_seq=0):
                if await request.is_disconnected():
                    return
                yield chunk
        async for chunk in session_heartbeat_stream(session_id, start_seq=1000):
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
    _: AuthDep = None,
):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    message = (message or "").strip()

    async def event_source():
        yield "retry: 4000\n\n"
        if message:
            async for chunk in run_turn(agent_id, message, user_id, start_seq=0):
                if await request.is_disconnected():
                    return
                yield chunk
        async for chunk in heartbeat_stream(agent_id, start_seq=1000):
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
async def agent_state(agent_id: str, _: AuthDep):
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"agent_id": agent_id, "busy": agent["busy"], "enabled": agent["enabled"]}
