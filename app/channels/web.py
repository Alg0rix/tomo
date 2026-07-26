"""Built-in web UI channel — SSE mapping for the coordinator agent loop.

The web chat SSE entrypoint is the FastAPI route in ``app/api/stream.py``
(``/api/sessions/{id}/chat/stream`` and ``/api/agents/{id}/chat/stream``),
which delegates to ``app/services/chat.py``. This module holds the
web-channel helper that consumes the internal event stream from
:func:`app.runtime.agent.loop.run_turn` and maps each event onto an SSE wire
event (via :func:`_fmt_sse`), persisting the matching history entry through
``store.append_session_history``.

Loop -> SSE mapping (UI contract — do not rename the wire events):

    | Loop kind     | SSE event                        | Persisted type  |
    |---------------|----------------------------------|-----------------|
    | thinking      | ``thinking``                     | ``thinking``    |
    | tool          | ``tool``                         | ``tool_call``   |
    | tool_result   | ``tool_result``                  | ``tool_output`` |
    | final         | ``delta`` (one chunk) + ``done`` | ``final``       |
    | error         | ``error``                        | ``error``       |

Every turn emits a leading ``state`` (busy true) and ``turn.start``, and a
trailing ``state`` (busy false) once the loop drains (success or error).

Coordinator-only: a session turn runs *only* ``coordinator_id``; the
multi-agent ``_pick_responders`` delegation path is intentionally unused for
the foundation thin vertical and ``agent_ids`` membership is unchanged. The
LLM defaults to the mock provider (``TOMO_LLM_PROVIDER=mock``) so turns work
with no API keys.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

from app.runtime.agent.loop import run_turn as _agent_run_turn
from app.services.store import store


def _fmt_sse(event: dict[str, Any]) -> str:
    """Serialise one SSE event to the ``event:``/``data:``/``id:`` wire form."""
    name = event["event"]
    data = json.dumps(event.get("data", {}), separators=(",", ":"))
    seq = event.get("seq")
    lines = [f"event: {name}", f"data: {data}"]
    if seq is not None:
        lines.append(f"id: {seq}")
    return "\n".join(lines) + "\n\n"


def _now() -> float:
    return time.time()


def _map_event(
    ev: dict[str, Any],
    coordinator_id: str,
    agent_name: str,
    seq: int,
    turn_id: str,
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Map one loop event to ``(sse_chunks, history_entries, next_seq)``.

    ``seq`` is the id of the last emitted chunk; this appends one or more
    chunks with increasing ids and returns the new last-seq. ``turn_id`` is
    echoed on the ``done`` event so it matches the leading ``turn.start``.
    Each history entry matches the ``ChatEntry`` replay shape (type/content/
    agent_id/function/params/error/ts). Unknown event kinds are a no-op so a
    future loop event can't crash the stream.
    """
    kind = ev["kind"]
    chunks: list[str] = []
    entries: list[dict[str, Any]] = []

    if kind == "thinking":
        seq += 1
        chunks.append(
            _fmt_sse(
                {
                    "event": "thinking",
                    "data": {"content": ev["content"], "agent_id": coordinator_id},
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "thinking",
                "content": ev["content"],
                "agent_id": coordinator_id,
                "ts": _now(),
            }
        )
    elif kind == "tool":
        seq += 1
        chunks.append(
            _fmt_sse(
                {
                    "event": "tool",
                    "data": {
                        "tool": ev["tool"],
                        "args": ev["args"],
                        "agent_id": coordinator_id,
                    },
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "tool_call",
                "function": ev["tool"],
                "params": ev["args"],
                "agent_id": coordinator_id,
                "ts": _now(),
            }
        )
    elif kind == "tool_result":
        seq += 1
        chunks.append(
            _fmt_sse(
                {
                    "event": "tool_result",
                    "data": {
                        "tool": ev["tool"],
                        "result": ev["result"],
                        "error": ev["error"],
                        "agent_id": coordinator_id,
                    },
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "tool_output",
                "content": ev["result"],
                "function": ev["tool"],
                "error": ev["error"],
                "agent_id": coordinator_id,
                "ts": _now(),
            }
        )
    elif kind == "final":
        content = ev["content"] or ""
        seq += 1
        chunks.append(
            _fmt_sse(
                {
                    "event": "delta",
                    "data": {"content": content, "agent_id": coordinator_id},
                    "seq": seq,
                }
            )
        )
        seq += 1
        chunks.append(
            _fmt_sse(
                {
                    "event": "done",
                    "data": {
                        "turn_id": turn_id,
                        "content": content,
                        "agent": agent_name,
                        "agent_id": coordinator_id,
                    },
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "final",
                "content": content,
                "agent_id": coordinator_id,
                "ts": _now(),
            }
        )
    elif kind == "error":
        msg = ev["message"]
        seq += 1
        chunks.append(
            _fmt_sse(
                {
                    "event": "error",
                    "data": {"message": msg, "agent_id": coordinator_id},
                    "seq": seq,
                }
            )
        )
        entries.append(
            {
                "type": "error",
                "content": msg,
                "agent_id": coordinator_id,
                "ts": _now(),
            }
        )

    return chunks, entries, seq


async def stream_turn_sse(
    session_id: str,
    coordinator_id: str,
    message: str,
    start_seq: int,
) -> AsyncIterator[str]:
    """Run one coordinator turn and yield SSE chunks, persisting history.

    The user message is persisted *before* the loop so it is part of the
    history fed to ``build_messages`` (``user_message=None`` is passed to the
    loop to avoid a duplicate). Each loop event is mapped to an SSE event and
    a matching ``append_session_history`` entry via :func:`_map_event`, and the
    history entry is appended *before* the SSE chunk is yielded — so a
    disconnect the instant a chunk is seen still leaves durable history
    (notably ``tool_call`` / ``final``). ``state`` busy true/false and
    ``turn.start`` are always emitted; a missing agent is surfaced as an SSE
    ``error`` so the stream stays well-formed.

    Busy cleanup: ``set_busy(coordinator_id, False)`` runs in a ``finally``
    that does **not** yield — yielding inside ``finally`` is unsafe on
    GeneratorExit/cancel and can leave the stream half-emitted. The trailing
    busy-false ``state`` is yielded *after* the try/finally completes normally,
    so on a clean drain the UI still sees busy go false; on cancel/disconnect
    the ``finally`` still clears busy even though the trailing SSE is skipped.
    The route (``app/api/stream.py``) and ``app/services/chat.py`` wrap this
    generator in ``contextlib.aclosing`` so a client disconnect closes the
    generator promptly and this ``finally`` runs synchronously.
    """
    seq = start_seq
    try:
        agent = store.get_agent(coordinator_id)
        agent_name = (agent or {}).get("name", coordinator_id)

        store.set_busy(coordinator_id, True)
        seq += 1
        yield _fmt_sse(
            {"event": "state", "data": {"agent_id": coordinator_id, "busy": True}, "seq": seq}
        )

        turn_id = f"turn_{uuid.uuid4().hex[:8]}"
        seq += 1
        yield _fmt_sse(
            {
                "event": "turn.start",
                "data": {
                    "turn_id": turn_id,
                    "agent": agent_name,
                    "agent_id": coordinator_id,
                    "delegate": False,
                },
                "seq": seq,
            }
        )

        if not agent:
            msg = f"Agent not found: {coordinator_id}"
            # Persist before yield: the error entry is durable even if the
            # client disconnects the moment the SSE chunk is sent.
            store.append_session_history(
                session_id,
                {"type": "error", "content": msg, "agent_id": coordinator_id, "ts": _now()},
            )
            seq += 1
            yield _fmt_sse(
                {"event": "error", "data": {"message": msg, "agent_id": coordinator_id}, "seq": seq}
            )
        else:
            store.append_session_history(
                session_id, {"type": "user", "content": message, "ts": _now()}
            )
            history = store.get_session_history(session_id)
            async for ev in _agent_run_turn(
                None,
                history=history,
                agent_id=coordinator_id,
                session_id=session_id,
            ):
                chunks, entries, seq = _map_event(
                    ev, coordinator_id, agent_name, seq, turn_id
                )
                # Persist before yield: each history entry is durable before its
                # SSE chunk is sent, so a disconnect after seeing an event still
                # has the matching history (tool_call / final / error).
                for entry in entries:
                    store.append_session_history(session_id, entry)
                for chunk in chunks:
                    yield chunk
    finally:
        # Clear busy unconditionally — even on cancel/disconnect (GeneratorExit),
        # where the trailing busy-false state below is never reached. Do NOT yield
        # here: yielding inside `finally` on GeneratorExit is unsafe (the yield is
        # ignored / raises) and can leave the stream half-emitted.
        store.set_busy(coordinator_id, False)

    # Reached only on normal completion (loop drained or agent-not-found error
    # emitted). The trailing busy-false `state` is emitted AFTER the try/finally
    # so it never runs during cancellation; busy was already cleared above.
    seq += 1
    yield _fmt_sse(
        {"event": "state", "data": {"agent_id": coordinator_id, "busy": False}, "seq": seq}
    )


__all__ = ["_fmt_sse", "stream_turn_sse"]
