"""Web chat SSE wiring — public streaming entrypoints over the agent loop.

Thin orchestration: resolve the session/coordinator for an incoming chat
message, then delegate the loop->SSE mapping + persistence (including swarm
``delegate`` / ``@mention`` handoff) to :func:`app.channels.web.stream_turn_sse`.
The heartbeat/state streams and the user-message recorder live here too; the
SSE formatter ``_fmt_sse`` is re-exported from the web channel for callers that
still reach for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import re

from app.channels.web import _fmt_sse, stream_turn_sse

from .store import store

logger = logging.getLogger(__name__)

_REPLAY_MAX = 256


def _extract_seq(chunk: str) -> int | None:
    """Extract the ``id:`` seq from an SSE wire-format chunk."""
    for line in chunk.split("\n", 4):
        if line.startswith("id: "):
            try:
                return int(line[4:])
            except ValueError:
                pass
    return None


# ── Active turn registry ─────────────────────────────────────────────
# Decouples the agent turn from the SSE connection so that a client
# disconnect (page refresh) does NOT kill the turn.  The turn runs as a
# background task; SSE streams subscribe to a broadcast queue.
#
# Adapted from evonic's realtime.py: per-connection ring buffer with
# replay-on-subscribe so a reconnecting client sees events it missed.
@dataclass
class _ActiveTurn:
    session_id: str
    _consumers: list[asyncio.Queue] = field(default_factory=list)
    _replay: list[tuple[int | None, str]] = field(default_factory=list)
    task: asyncio.Task | None = None

    def subscribe(self, after_seq: int = 0) -> asyncio.Queue:
        """Subscribe to live events.

        If *after_seq* > 0, replay buffered chunks with ``seq > after_seq``
        into the queue first — so a reconnecting client catches up on
        events it missed during the disconnect gap (evonic pattern).
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        # Replay: push past chunks the client hasn't seen yet.
        if after_seq > 0:
            for seq, chunk in self._replay:
                if seq is not None and seq > after_seq:
                    try:
                        q.put_nowait(chunk)
                    except asyncio.QueueFull:
                        break
        elif after_seq == 0:
            # Fresh connect: replay everything (client dedups by seq).
            for _seq, chunk in self._replay:
                try:
                    q.put_nowait(chunk)
                except asyncio.QueueFull:
                    break
        self._consumers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._consumers:
            self._consumers.remove(q)

    def _broadcast(self, chunk: str | None) -> None:
        if chunk is not None:
            seq = _extract_seq(chunk)
            self._replay.append((seq, chunk))
            if len(self._replay) > _REPLAY_MAX:
                self._replay = self._replay[-_REPLAY_MAX:]
        for q in list(self._consumers):
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    def finish(self) -> None:
        self._broadcast(None)
        self._consumers.clear()


_active_turns: dict[str, _ActiveTurn] = {}


def get_active_session_turn(session_id: str) -> _ActiveTurn | None:
    """Return the active turn for *session_id* if one is running."""
    turn = _active_turns.get(session_id)
    if turn and turn.task and not turn.task.done():
        return turn
    _active_turns.pop(session_id, None)
    return None


async def start_session_turn(
    session_id: str, message: str, user_id: str, start_seq: int = 0
) -> asyncio.Queue:
    """Start a background agent turn and return a subscription queue.

    The turn runs independently of any SSE connection.  Multiple clients
    can subscribe to the same turn (e.g. after a page refresh).
    """
    session = store.get_session(session_id)
    if not session:
        raise ValueError(f"Session not found: {session_id}")
    coordinator_id = _coordinator_for(session)
    if not coordinator_id:
        raise ValueError(f"No coordinator for session: {session_id}")

    turn = _ActiveTurn(session_id=session_id)

    async def _runner() -> None:
        try:
            async with contextlib.aclosing(
                stream_turn_sse(session_id, coordinator_id, message, start_seq)
            ) as agen:
                async for chunk in agen:
                    turn._broadcast(chunk)
        except Exception as exc:
            logger.exception("background turn failed session_id=%s", session_id)
            turn._broadcast(
                _fmt_sse(
                    {
                        "event": "error",
                        "data": {"message": f"Turn failed: {exc}"},
                        "seq": 9998,
                    }
                )
            )
        finally:
            turn.finish()
            _active_turns.pop(session_id, None)
            logger.info("background turn done session_id=%s", session_id)

    turn.task = asyncio.create_task(_runner())
    _active_turns[session_id] = turn
    return turn.subscribe()


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
    """Stream one turn for a session (swarm or single-agent).

    Starts on ``coordinator_id``; ``stream_turn_sse`` may hand off to a session
    member via ``delegate`` tool or leading ``@mention``.
    """
    session = store.get_session(session_id)
    if not session:
        # The route validates existence and 404s first; stay defensive so a
        # missing session still yields a well-formed error stream.
        logger.warning("turn rejected session_id=%s reason=session not found", session_id)
        seq = start_seq
        seq += 1
        yield _fmt_sse(
            {"event": "error", "data": {"message": "Session not found"}, "seq": seq}
        )
        return
    coordinator_id = _coordinator_for(session)
    if not coordinator_id:
        logger.warning("turn rejected session_id=%s reason=no coordinator", session_id)
        seq = start_seq
        seq += 1
        yield _fmt_sse(
            {"event": "error", "data": {"message": "Session has no coordinator"}, "seq": seq}
        )
        return
    logger.info(
        "turn accept session_id=%s user_id=%s coordinator_id=%s message=%r",
        session_id,
        user_id,
        coordinator_id,
        (message or "")[:120],
    )
    # aclosing ensures that closing this generator (on disconnect — see the
    # route-level aclosing in app/api/stream.py) cascades into stream_turn_sse's
    # `finally`, which clears the coordinator's busy flag synchronously instead
    # of leaving the inner generator suspended until garbage collection.
    async with contextlib.aclosing(
        stream_turn_sse(session_id, coordinator_id, message, start_seq)
    ) as agen:
        async for chunk in agen:
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
    async with contextlib.aclosing(
        stream_turn_sse(session_id, agent_id, message, start_seq)
    ) as agen:
        async for chunk in agen:
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
    "get_active_session_turn",
    "heartbeat_stream",
    "record_session_user_message",
    "run_session_turn",
    "run_turn",
    "session_heartbeat_stream",
    "start_session_turn",
]
