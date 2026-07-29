"""Built-in web UI channel — SSE turn orchestration + swarm handoff.

The web chat SSE entrypoint is the FastAPI route in ``app/api/stream.py``,
which delegates to ``app/services/chat.py``. This module runs coordinator or
member ``run_turn`` loops, maps events via :mod:`app.channels.sse_map`, and
persists history. See that module for the loop-kind → SSE event table.

Swarm handoff: a leading ``@member`` (session member) skips the coordinator and
runs the target agent. A successful coordinator ``delegate`` tool emits SSE
``delegate`` then a nested ``run_turn`` for the target. Non-members are rejected
by the tool / ignored for mentions (coordinator continues).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator

from app.channels.sse_map import fmt_sse, map_loop_event, now
from app.runtime.agent.loop import run_turn as _agent_run_turn
from app.runtime.coordinator.router import parse_leading_mention, resolve_target
from app.runtime.session_title import (
    first_user_and_final,
    generate_session_title,
    llm_title_skip_reason,
)
from app.runtime.tools import delegate as delegate_tool
from app.services.store import store

logger = logging.getLogger(__name__)

# Back-compat alias for callers/tests that imported ``_fmt_sse`` from here.
_fmt_sse = fmt_sse


def _session_agents(session: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Agents available for routing (delegate / @mention).

    Swarm sessions use every **currently enabled** agent (live). A newly
    created or re-enabled agent is routable on the next message without
    editing the session. Solo sessions stay fixed to their one member.
    """
    sid = (session.get("id") or "").strip()
    if sid:
        try:
            # Persist live membership so API/UI stay in sync with routing.
            from app.models.mixins import sessions as sessions_store

            # store facade holds the connection; prefer public resolve.
            live = store.get_session(sid)
            if live:
                session = live
        except Exception:
            pass

    session_ids = [aid for aid in (session.get("agent_ids") or []) if isinstance(aid, str)]
    is_swarm = bool(session.get("is_swarm")) or len(session_ids) != 1
    try:
        enabled_ids = store.list_enabled_agent_ids()
    except Exception:
        enabled_ids = []

    if is_swarm and enabled_ids:
        ids = list(enabled_ids)
        # Coordinator first when known.
        coord = (session.get("coordinator_id") or session.get("agent_id") or "").strip()
        if coord and coord in ids:
            ids = [coord] + [a for a in ids if a != coord]
    else:
        ids = list(session_ids)

    agents: list[dict[str, Any]] = []
    for aid in ids:
        agent = store.get_agent(aid)
        if agent and agent.get("enabled", True):
            agents.append(agent)
    return [a["id"] for a in agents], agents


def _agent_label(agent_id: str) -> str:
    agent = store.get_agent(agent_id)
    return (agent or {}).get("name", agent_id)


def _delegate_payload(
    *,
    from_id: str,
    to_id: str,
    reason: str,
) -> dict[str, Any]:
    to_name = _agent_label(to_id)
    return {
        "from": from_id,
        "to": to_id,
        "reason": reason,
        "agent_id": to_id,
        "agent": to_name,
        "content": f"Handing off to {to_name}",
    }


async def _emit_delegate(
    session_id: str,
    *,
    from_id: str,
    to_id: str,
    reason: str,
    seq: int,
) -> AsyncIterator[tuple[str, int]]:
    """Persist + yield one ``delegate`` SSE event; yields ``(chunk, seq)``."""
    data = _delegate_payload(from_id=from_id, to_id=to_id, reason=reason)
    store.append_session_history(
        session_id,
        {
            "type": "delegate",
            "content": data["content"],
            "agent_id": to_id,
            "from": from_id,
            "to": to_id,
            "reason": reason,
            "ts": now(),
        },
    )
    seq += 1
    yield fmt_sse({"event": "delegate", "data": data, "seq": seq}), seq


def _last_user_content(history: list[dict[str, Any]] | None) -> str:
    if not history:
        return ""
    for entry in reversed(history):
        if entry.get("type") == "user":
            return str(entry.get("content") or "")
    return ""


def _history_before_last_user(
    history: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Prior complete turns only (drop last user + any trailing tool trail)."""
    if not history:
        return []
    last_user_i = None
    for i, entry in enumerate(history):
        if entry.get("type") == "user":
            last_user_i = i
    if last_user_i is None:
        return list(history)
    return list(history[:last_user_i])


def _handoff_member_prompt(*, from_id: str, reason: str, user_request: str) -> str:
    """Clear task brief so the member *does the work* instead of re-delegating."""
    from_name = _agent_label(from_id)
    reason_s = (reason or "").strip() or "Handle the user's request."
    user_s = (user_request or "").strip()
    parts = [
        f"You received a handoff from {from_name}.",
        f"Task: {reason_s}",
        "Do the work yourself now (run tools as needed). Do not delegate again "
        "unless you truly cannot complete it.",
    ]
    if user_s:
        parts.append(f"User request:\n{user_s}")
    return "\n\n".join(parts)


async def _emit_member_turn_start(
    *,
    to_id: str,
    turn_id: str,
    seq: int,
) -> AsyncIterator[tuple[str, int]]:
    """Announce the nested member agent so the UI switches avatar/name."""
    seq += 1
    yield (
        fmt_sse(
            {
                "event": "state",
                "data": {
                    "agent_id": to_id,
                    "agent": _agent_label(to_id),
                    "busy": True,
                },
                "seq": seq,
            }
        ),
        seq,
    )
    seq += 1
    yield (
        fmt_sse(
            {
                "event": "turn.start",
                "data": {
                    "turn_id": turn_id,
                    "agent": _agent_label(to_id),
                    "agent_id": to_id,
                    "delegate": True,
                },
                "seq": seq,
            }
        ),
        seq,
    )


async def _drain_agent_turn(
    session_id: str,
    agent_id: str,
    *,
    user_message: str | None,
    history: list[dict[str, Any]] | None,
    seq: int,
    turn_id: str,
    busy_ids: set[str],
) -> AsyncIterator[tuple[str, int]]:
    """Run ``run_turn`` for ``agent_id``, mapping/persisting events.

    The loop handles subagent delegation internally: when the model calls
    ``delegate``, a nested ``run_turn`` runs for the target and its events
    are re-emitted tagged with the target ``agent_id``. This function just
    maps every event (resolving per-event attribution) and persists history.
    Yields ``(sse_chunk, seq)``.
    """
    agent_name = _agent_label(agent_id)
    store.set_busy(agent_id, True)
    busy_ids.add(agent_id)

    if history is None:
        history = store.get_session_history(session_id)

    async for ev in _agent_run_turn(
        user_message,
        history=history,
        agent_id=agent_id,
        session_id=session_id,
    ):
        # Nested subagent events carry their own agent_id for attribution.
        ev_agent_id = ev.get("agent_id") or agent_id
        if ev_agent_id != agent_id:
            ev_agent_name = _agent_label(ev_agent_id)
            if ev_agent_id not in busy_ids:
                store.set_busy(ev_agent_id, True)
                busy_ids.add(ev_agent_id)
        else:
            ev_agent_name = agent_name

        # Delegate events need the target agent's name, not the parent's.
        if ev.get("kind") == "delegate":
            to_id = ev.get("to") or ""
            if to_id:
                ev["to_name"] = _agent_label(to_id)

        chunks, entries, seq = map_loop_event(
            ev, ev_agent_id, ev_agent_name, seq, turn_id
        )
        for entry in entries:
            store.append_session_history(session_id, entry)
        for chunk in chunks:
            yield chunk, seq


async def _maybe_upgrade_title(
    session_id: str, seq: int
) -> AsyncIterator[tuple[str, int]]:
    """LLM session-title upgrade; yields ``(chunk, seq)`` when a title changes."""
    session = store.get_session(session_id)
    history = store.get_session_history(session_id)
    skip = llm_title_skip_reason(session, history)
    if skip:
        logger.info(
            "session title skip session_id=%s reason=%s title=%r",
            session_id,
            skip,
            (session or {}).get("title"),
        )
        return
    pair = first_user_and_final(history)
    if not pair:
        logger.info(
            "session title skip session_id=%s reason=missing user/final pair",
            session_id,
        )
        return
    logger.info(
        "session title generating session_id=%s provisional=%r",
        session_id,
        (session or {}).get("title"),
    )
    llm_title = await generate_session_title(pair[0], pair[1])
    if not llm_title:
        logger.warning(
            "session title unchanged session_id=%s kept=%r",
            session_id,
            (session or {}).get("title"),
        )
        return
    store.set_session_title(session_id, llm_title)
    logger.info(
        "session title saved session_id=%s title=%r",
        session_id,
        llm_title,
    )
    seq += 1
    yield (
        fmt_sse(
            {
                "event": "session",
                "data": {"session_id": session_id, "title": llm_title},
                "seq": seq,
            }
        ),
        seq,
    )


async def stream_turn_sse(
    session_id: str,
    coordinator_id: str,
    message: str,
    start_seq: int,
    attachment_ids: list[str] | None = None,
) -> AsyncIterator[str]:
    """Run one session turn and yield SSE chunks, persisting history.

    Supports coordinator turns, ``@mention`` force-handoff to session members,
    and mid-turn ``delegate`` tool handoffs with nested member ``run_turn``.

    Only one turn may run per session at a time; concurrent streams get an
    immediate error so the client can re-queue the message.
    """
    seq = start_seq
    busy_ids: set[str] = set()
    ctx_token = None
    wp_tokens = None
    turn_locked = store.try_begin_session_turn(session_id)
    if not turn_locked:
        logger.warning(
            "turn rejected session_id=%s reason=session busy concurrent turn",
            session_id,
        )
        seq += 1
        yield fmt_sse(
            {
                "event": "error",
                "data": {
                    "message": (
                        "Session is busy with another turn. "
                        "Your message was not accepted — try again when idle."
                    ),
                    "code": "session_busy",
                    "agent_id": coordinator_id,
                },
                "seq": seq,
            }
        )
        return

    logger.info(
        "turn begin session_id=%s coordinator_id=%s start_seq=%s message=%r",
        session_id,
        coordinator_id,
        start_seq,
        (message or "")[:120],
    )
    try:
        session = store.get_session(session_id) or {}
        member_ids, member_agents = _session_agents(session)
        ctx_token = delegate_tool.bind_context(
            agent_ids=member_ids, agents=member_agents
        )

        mention, mention_rest = parse_leading_mention(message)
        force_target: str | None = None
        if mention:
            force_target = resolve_target(
                agent_ids=member_ids, agents=member_agents, query=mention
            )

        # Workplace for this turn: message token wins, else session default.
        # Session default is usually a **local** workplace (chat folder context).
        # Tunnel/SSH stay reachable via agents that own them or workplace= on tools.
        wp_tokens = None
        try:
            from app.runtime.tools.workplace_ctx import (
                bind_workplace,
                reset_workplace,
                strip_workplace_hint,
            )

            wps = store.list_workplaces()
            body_for_wp = mention_rest if force_target else message
            stripped, wp_hint = strip_workplace_hint(body_for_wp, wps)
            session_wp = (session.get("workplace_id") or "").strip()
            if wp_hint:
                if force_target:
                    mention_rest = stripped
                else:
                    # Keep full message for coordinator; still bind workplace hint.
                    pass
                wp_tokens = bind_workplace(hint=wp_hint)
                logger.info(
                    "workplace hint session_id=%s hint=%r",
                    session_id,
                    wp_hint,
                )
            elif session_wp:
                wp_tokens = bind_workplace(workplace_id=session_wp)
                logger.info(
                    "workplace session default session_id=%s workplace_id=%s",
                    session_id,
                    session_wp,
                )
            else:
                # Chat chose Tomo work dir (~/tomo/<agent>) — ignore agent local WP.
                wp_tokens = bind_workplace(force_work_dir=True)
                logger.info(
                    "workplace session force_work_dir session_id=%s",
                    session_id,
                )
        except Exception:
            wp_tokens = None

        will_delegate = bool(force_target)
        start_agent_id = force_target or coordinator_id
        start_agent_name = _agent_label(start_agent_id)

        store.set_busy(coordinator_id, True)
        busy_ids.add(coordinator_id)
        seq += 1
        yield fmt_sse(
            {
                "event": "state",
                "data": {"agent_id": coordinator_id, "busy": True},
                "seq": seq,
            }
        )

        turn_id = f"turn_{uuid.uuid4().hex[:8]}"
        seq += 1
        yield fmt_sse(
            {
                "event": "turn.start",
                "data": {
                    "turn_id": turn_id,
                    "agent": start_agent_name,
                    "agent_id": start_agent_id,
                    "delegate": will_delegate,
                },
                "seq": seq,
            }
        )

        if not store.get_agent(coordinator_id):
            msg = f"Agent not found: {coordinator_id}"
            store.append_session_history(
                session_id,
                {
                    "type": "error",
                    "content": msg,
                    "agent_id": coordinator_id,
                    "ts": now(),
                },
            )
            seq += 1
            yield fmt_sse(
                {
                    "event": "error",
                    "data": {"message": msg, "agent_id": coordinator_id},
                    "seq": seq,
                }
            )
        else:
            user_content = message
            if attachment_ids:
                info_lines = "\n".join(
                    f"[Attached: {a.get('original_name') or a.get('filename')} "
                    f"({a.get('mime_type') or 'application/octet-stream'}, {a.get('size_bytes', 0)}B) "
                    f"id={a.get('id')} path={a.get('file_path')}]"
                    for a in (store.get_attachment(aid) for aid in attachment_ids)
                    if a
                )
                if info_lines:
                    user_content = info_lines + "\n\n" + message
            new_title = store.append_session_history(
                session_id, {"type": "user", "content": user_content, "ts": now()}
            )
            if new_title:
                logger.info(
                    "session title provisional session_id=%s title=%r",
                    session_id,
                    new_title,
                )
                seq += 1
                yield fmt_sse(
                    {
                        "event": "session",
                        "data": {
                            "session_id": session_id,
                            "title": new_title,
                        },
                        "seq": seq,
                    }
                )

            if force_target:
                logger.info(
                    "mention handoff session_id=%s from=%s to=%s",
                    session_id,
                    coordinator_id,
                    force_target,
                )
                async for chunk, seq in _emit_delegate(
                    session_id,
                    from_id=coordinator_id,
                    to_id=force_target,
                    reason="mention",
                    seq=seq,
                ):
                    yield chunk
                store.set_busy(coordinator_id, False)
                # Persist full ``@ops …`` user row; feed the member the stripped
                # prompt without the user row or the just-written handoff row.
                hist = store.get_session_history(session_id)
                hist_for_member = _history_before_last_user(hist)
                member_prompt = mention_rest.strip() or message
                if attachment_ids:
                    info_lines = "\n".join(
                        f"[Attached: {a.get('original_name') or a.get('filename')} "
                        f"({a.get('mime_type') or 'application/octet-stream'}, {a.get('size_bytes', 0)}B) "
                        f"id={a.get('id')} path={a.get('file_path')}]"
                        for a in (store.get_attachment(aid) for aid in attachment_ids)
                        if a
                    )
                    if info_lines:
                        member_prompt = info_lines + "\n\n" + member_prompt
                async for chunk, seq in _emit_member_turn_start(
                    to_id=force_target, turn_id=turn_id, seq=seq
                ):
                    yield chunk
                async for chunk, seq in _drain_agent_turn(
                    session_id,
                    force_target,
                    user_message=member_prompt,
                    history=hist_for_member,
                    seq=seq,
                    turn_id=turn_id,
                    busy_ids=busy_ids,
                ):
                    yield chunk
            else:
                async for chunk, seq in _drain_agent_turn(
                    session_id,
                    coordinator_id,
                    user_message=None,
                    history=store.get_session_history(session_id),
                    seq=seq,
                    turn_id=turn_id,
                    busy_ids=busy_ids,
                ):
                    yield chunk

            async for chunk, seq in _maybe_upgrade_title(session_id, seq):
                yield chunk
    finally:
        if ctx_token is not None:
            delegate_tool.reset_context(ctx_token)
        try:
            if wp_tokens is not None:
                from app.runtime.tools.workplace_ctx import reset_workplace

                reset_workplace(wp_tokens)
        except Exception:
            pass
        for aid in list(busy_ids):
            store.set_busy(aid, False)
        store.set_busy(coordinator_id, False)
        if turn_locked:
            store.end_session_turn(session_id)

    seq += 1
    yield fmt_sse(
        {
            "event": "state",
            "data": {"agent_id": coordinator_id, "busy": False},
            "seq": seq,
        }
    )
    logger.info(
        "turn end session_id=%s coordinator_id=%s last_seq=%s",
        session_id,
        coordinator_id,
        seq,
    )


__all__ = ["_fmt_sse", "stream_turn_sse"]
