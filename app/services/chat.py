"""Stub chat engine — simulates agent turns for the streaming UI."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, AsyncIterator

from .store import store


def _fmt_sse(event: dict[str, Any]) -> str:
    name = event["event"]
    data = json.dumps(event.get("data", {}), separators=(",", ":"))
    seq = event.get("seq")
    lines = [f"event: {name}", f"data: {data}"]
    if seq is not None:
        lines.append(f"id: {seq}")
    return "\n".join(lines) + "\n\n"


def _parse_mention(message: str, agent_ids: list[str]) -> str | None:
    m = re.search(r"@(\w+)", message, re.IGNORECASE)
    if not m:
        return None
    token = m.group(1).lower()
    for aid in agent_ids:
        if aid.lower() == token:
            return aid
        agent = store.get_agent(aid)
        if agent and agent.get("name", "").lower().startswith(token):
            return aid
    return None


def _pick_responders(session: dict[str, Any], message: str) -> list[str]:
    agent_ids = session.get("agent_ids") or [session.get("coordinator_id") or session.get("agent_id")]
    coord = session.get("coordinator_id") or agent_ids[0]
    mentioned = _parse_mention(message, agent_ids)
    if mentioned and mentioned != coord:
        return [coord, mentioned]
    if len(agent_ids) <= 1:
        return [coord]
    others = [a for a in agent_ids if a != coord]
    delegate = others[0] if others else None
    if delegate and len(message) > 20:
        return [coord, delegate]
    return [coord]


async def _stream_agent_turn(
    agent_id: str,
    message: str,
    seq: int,
    *,
    is_delegate: bool = False,
) -> AsyncIterator[tuple[str, int, str | None]]:
    """Yield (sse_chunk, seq, done_reply). done_reply set only on final done event."""
    agent = store.get_agent(agent_id)
    agent_name = (agent or {}).get("name", agent_id)

    store.set_busy(agent_id, True)
    seq += 1
    yield _fmt_sse({"event": "state", "data": {"agent_id": agent_id, "busy": True}, "seq": seq}), seq, None

    turn_id = f"turn_{uuid.uuid4().hex[:8]}"
    seq += 1
    yield _fmt_sse({
        "event": "turn.start",
        "data": {"turn_id": turn_id, "agent": agent_name, "agent_id": agent_id, "delegate": is_delegate},
        "seq": seq,
    }), seq, None

    thinking = (
        f"Routing sub-task to {agent_name}…" if is_delegate
        else f"Okay — you asked about “{message[:60]}”. Let me reason through this."
    )
    for i in range(0, len(thinking), 18):
        await asyncio.sleep(0.03)
        seq += 1
        yield _fmt_sse({
            "event": "thinking",
            "data": {"content": thinking[i:i + 18], "agent_id": agent_id},
            "seq": seq,
        }), seq, None

    if not is_delegate and len(message) > 6:
        await asyncio.sleep(0.2)
        seq += 1
        yield _fmt_sse({"event": "tool", "data": {"tool": "recall", "args": {"query": message[:40]}, "agent_id": agent_id}, "seq": seq}), seq, None
        await asyncio.sleep(0.35)
        seq += 1
        yield _fmt_sse({
            "event": "tool_result",
            "data": {"tool": "recall", "result": "No prior memory matched; proceeding fresh.", "error": False, "agent_id": agent_id},
            "seq": seq,
        }), seq, None

    if is_delegate:
        reply = (
            f"**{agent_name}** (delegated): I can take this part of the task. "
            f"In a live swarm, the coordinator would hand off context here.\n\n"
            f"Re: *{message.strip()[:80]}*"
        )
    else:
        reply = (
            f"This is a **stubbed** response from **{agent_name}**. The agent backend isn't wired up yet, "
            f"so I'm simulating a turn to demonstrate the streaming UI.\n\n"
            f"You said: *{message.strip()}*\n\n"
            f"When the real coordinator runtime is connected, this is where live model output, tool "
            f"calls, and swarm delegation will stream."
        )
    for i in range(0, len(reply), 6):
        await asyncio.sleep(0.02)
        is_final = i + 6 >= len(reply)
        seq += 1
        yield _fmt_sse({
            "event": "delta",
            "data": {"content": reply[i:i + 6], "is_final": is_final, "agent_id": agent_id, "agent": agent_name},
            "seq": seq,
        }), seq, None

    store.set_busy(agent_id, False)
    seq += 1
    yield _fmt_sse({"event": "done", "data": {"turn_id": turn_id, "content": reply, "agent_id": agent_id, "agent": agent_name}, "seq": seq}), seq, reply
    seq += 1
    yield _fmt_sse({"event": "state", "data": {"agent_id": agent_id, "busy": False}, "seq": seq}), seq, None


async def _stream_turn(agent_id: str, message: str, user_id: str, seq: int) -> AsyncIterator[str]:
    async for chunk, seq, _ in _stream_agent_turn(agent_id, message, seq):
        yield chunk


async def _stream_session_turn(session_id: str, message: str, user_id: str, seq: int) -> AsyncIterator[str]:
    session = store.get_session(session_id)
    if not session:
        yield _fmt_sse({"event": "error", "data": {"message": "Session not found"}, "seq": seq})
        return

    for i, agent_id in enumerate(_pick_responders(session, message)):
        if i > 0:
            agent = store.get_agent(agent_id)
            name = (agent or {}).get("name", agent_id)
            seq += 1
            yield _fmt_sse({
                "event": "delegate",
                "data": {"agent_id": agent_id, "agent": name, "content": f"Handing off to {name}…"},
                "seq": seq,
            })
            await asyncio.sleep(0.15)

        reply = None
        async for chunk, seq, done_reply in _stream_agent_turn(agent_id, message, seq, is_delegate=i > 0):
            yield chunk
            if done_reply:
                reply = done_reply

        store.append_session_history(session_id, {
            "type": "final",
            "content": reply or f"Response from {agent_id}",
            "agent_id": agent_id,
            "ts": time.time(),
        })


def record_user_message(agent_id: str, user_id: str, message: str) -> str:
    store.append_history(agent_id, user_id, {"type": "user", "content": message, "ts": time.time()})
    return store.get_or_create_session(agent_id, user_id)


def record_assistant_message(agent_id: str, user_id: str, content: str) -> None:
    store.append_history(agent_id, user_id, {"type": "final", "content": content, "agent_id": agent_id, "ts": time.time()})


def record_session_user_message(session_id: str, message: str) -> None:
    store.append_session_history(session_id, {"type": "user", "content": message, "ts": time.time()})


async def run_turn(agent_id: str, message: str, user_id: str, start_seq: int = 0) -> AsyncIterator[str]:
    record_user_message(agent_id, user_id, message)
    reply = None
    seq = start_seq
    async for chunk, seq, done_reply in _stream_agent_turn(agent_id, message, seq):
        yield chunk
        if done_reply:
            reply = done_reply
    record_assistant_message(
        agent_id,
        user_id,
        reply or (
            f"This is a **stubbed** response. The agent backend isn't wired up yet, "
            f"so I'm simulating a turn to demonstrate the streaming UI.\n\nYou said: *{message.strip()}*"
        ),
    )


async def run_session_turn(session_id: str, message: str, user_id: str, start_seq: int = 0) -> AsyncIterator[str]:
    record_session_user_message(session_id, message)
    async for chunk in _stream_session_turn(session_id, message, user_id, start_seq):
        yield chunk


async def heartbeat_stream(agent_id: str, start_seq: int = 0) -> AsyncIterator[str]:
    seq = start_seq
    agent = store.get_agent(agent_id)
    if agent:
        yield _fmt_sse({"event": "state", "data": {"agent_id": agent_id, "busy": agent["busy"]}, "seq": seq})
    while True:
        await asyncio.sleep(15)
        seq += 1
        yield _fmt_sse({"event": "heartbeat", "data": {}, "seq": seq})


async def session_heartbeat_stream(session_id: str, start_seq: int = 0) -> AsyncIterator[str]:
    seq = start_seq
    session = store.get_session(session_id)
    if session:
        for aid in session.get("agent_ids") or []:
            agent = store.get_agent(aid)
            if agent:
                yield _fmt_sse({"event": "state", "data": {"agent_id": aid, "busy": agent["busy"]}, "seq": seq})
                seq += 1
    while True:
        await asyncio.sleep(15)
        seq += 1
        yield _fmt_sse({"event": "heartbeat", "data": {}, "seq": seq})
