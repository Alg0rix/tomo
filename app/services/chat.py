"""Web chat SSE wiring — public streaming entrypoints over the coordinator
agent loop.

Thin orchestration: resolve the session/coordinator for an incoming chat
message, then delegate the loop->SSE mapping + persistence to the web-channel
helper :func:`app.channels.web.stream_turn_sse`. The heartbeat/state streams
and the user-message recorder live here too; the SSE formatter ``_fmt_sse``
is re-exported from the web channel for any caller that still reaches for it.

Coordinator-only: a session turn runs *only* ``coordinator_id``; multi-agent
delegation is intentionally unused for the foundation thin vertical and
``agent_ids`` membership is left unchanged. The LLM defaults to the mock
provider (``TOMO_LLM_PROVIDER=mock``) so turns work with no API keys.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from app.channels.web import _fmt_sse, stream_turn_sse

from .store import store


def _coordinator_for(session: dict[str, Any]) -> str | None:
    """Resolve the coordinator agent id for a session dict."""
    coord = session.get("coordinator_id") or session.get("agent_id")
    if coord:
        return coord
    ids = session.get("agent_ids") or []
    return ids[0] if ids else None


async def run_session_turn(
    session_id: str, message: str, user_id: str, start_seq: int = 0
) -> AsyncIterator[str]:
    """Stream one coordinator turn for a session (swarm or single-agent).

    Runs only ``coordinator_id`` and ignores multi-agent delegation;
    ``agent_ids`` membership is unchanged.
    """
    session = store.get_session(session_id)
    if not session:
        # The route validates existence and 404s first; stay defensive so a
        # missing session still yields a well-formed error stream.
        seq = start_seq
        seq += 1
        yield _fmt_sse(
            {"event": "error", "data": {"message": "Session not found"}, "seq": seq}
        )
        return
    coordinator_id = _coordinator_for(session)
    if not coordinator_id:
        seq = start_seq
        seq += 1
        yield _fmt_sse(
            {"event": "error", "data": {"message": "Session has no coordinator"}, "seq": seq}
        )
        return
    async for chunk in stream_turn_sse(session_id, coordinator_id, message, start_seq):
        yield chunk


async def run_turn(
    agent_id: str, message: str, user_id: str, start_seq: int = 0
) -> AsyncIterator[str]:
    """Stream one coordinator turn for an agent's single-agent session.

    Resolves (or creates) the agent's single-agent session, then delegates to
    the same coordinator-only turn wiring as :func:`run_session_turn`.
    """
    if not store.get_agent(agent_id):
        seq = start_seq
        seq += 1
        yield _fmt_sse(
            {
                "event": "error",
                "data": {"message": f"Agent not found: {agent_id}", "agent_id": agent_id},
                "seq": seq,
            }
        )
        return
    session_id = store.get_or_create_session(agent_id, user_id)
    async for chunk in stream_turn_sse(session_id, agent_id, message, start_seq):
        yield chunk


def record_session_user_message(session_id: str, message: str) -> None:
    """Persist a user message into a session's history (no agent turn)."""
    store.append_session_history(session_id, {"type": "user", "content": message})


async def heartbeat_stream(agent_id: str, start_seq: int = 0) -> AsyncIterator[str]:
    """Emit the agent's initial ``state`` then periodic ``heartbeat`` events."""
    seq = start_seq
    agent = store.get_agent(agent_id)
    if agent:
        yield _fmt_sse(
            {"event": "state", "data": {"agent_id": agent_id, "busy": agent["busy"]}, "seq": seq}
        )
    while True:
        await asyncio.sleep(15)
        seq += 1
        yield _fmt_sse({"event": "heartbeat", "data": {}, "seq": seq})


async def session_heartbeat_stream(
    session_id: str, start_seq: int = 0
) -> AsyncIterator[str]:
    """Emit each member's initial ``state`` then periodic ``heartbeat`` events."""
    seq = start_seq
    session = store.get_session(session_id)
    if session:
        for aid in session.get("agent_ids") or []:
            agent = store.get_agent(aid)
            if agent:
                yield _fmt_sse(
                    {"event": "state", "data": {"agent_id": aid, "busy": agent["busy"]}, "seq": seq}
                )
                seq += 1
    while True:
        await asyncio.sleep(15)
        seq += 1
        yield _fmt_sse({"event": "heartbeat", "data": {}, "seq": seq})


__all__ = [
    "_fmt_sse",
    "heartbeat_stream",
    "record_session_user_message",
    "run_session_turn",
    "run_turn",
    "session_heartbeat_stream",
]
