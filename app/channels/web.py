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

from app.channels.sse_map import fmt_sse, map_loop_event, now, session_busy_sse
from app.runtime.agent.loop import run_turn as _agent_run_turn
from app.runtime.coordinator.router import parse_leading_mention, resolve_target
from app.runtime.coordinator.swarm import run_swarm_turn
from app.runtime.coordinator.swarm import advise_swarm
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



_BARE_SWARM = frozenset({
    "swarm", "/swarm", "pakai swarm", "pake swarm", "use swarm", "swarm aja",
    "swarm dong", "pakai team", "pake team", "use a team", "use team", "gunakan swarm",
})


def _is_bare_swarm_optin(message: str | None) -> bool:
    return " ".join((message or "").casefold().strip(" .!?").split()) in _BARE_SWARM


def _previous_user_request(session_id: str) -> str:
    """Most recent earlier user message that is itself a task."""
    for entry in reversed(store.get_session_history(session_id) or []):
        if entry.get("type") != "user":
            continue
        text = str(entry.get("content") or "").strip()
        if text.startswith("/swarm "):
            text = text[7:].strip()
        if text and not _is_bare_swarm_optin(text):
            return text
    return ""

def _session_agents(session: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """Agents available for routing (delegate / @mention).

    Swarm sessions use every **currently enabled** agent (live). A newly
    created or re-enabled agent is routable on the next message without
    editing the session. Solo sessions stay fixed to their one member.
    """
    sid = (session.get("id") or "").strip()
    if sid:
        try:
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


def _accumulate_turn_tokens(
    token_acc: dict[str, int] | None, ev: dict[str, Any]
) -> None:
    """Fold prompt/completion totals from a final (or subagent final) event."""
    if token_acc is None:
        return
    kind = ev.get("kind")
    if kind not in ("final", "subagent_final"):
        return
    metrics = ev.get("metrics")
    if not isinstance(metrics, dict):
        return
    token_acc["prompt"] = int(token_acc.get("prompt") or 0) + int(
        metrics.get("prompt_tokens") or 0
    )
    token_acc["completion"] = int(token_acc.get("completion") or 0) + int(
        metrics.get("completion_tokens") or 0
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
    token_acc: dict[str, int] | None = None,
    origin: str | None = None,
    swarm_request: str | None = None,
    swarm_plan: dict[str, Any] | None = None,
) -> AsyncIterator[tuple[str, int]]:
    """Run ``run_turn`` for ``agent_id``, mapping/persisting events.

    The loop handles subagent delegation internally: when the model calls
    ``delegate``, a nested ``run_turn`` runs for the target and its events
    are re-emitted tagged with the target ``agent_id``. This function just
    maps every event (resolving per-event attribution) and persists history.
    Yields ``(sse_chunk, seq)``.

    When ``token_acc`` is provided (``{"prompt": 0, "completion": 0}``),
    cumulative in/out tokens from this drain (including nested subagents)
    are added onto it for Token Monitor.
    """
    agent_name = _agent_label(agent_id)
    store.set_busy(agent_id, True, session_id=session_id)
    busy_ids.add(agent_id)

    if history is None:
        history = store.get_session_history(session_id)

    if swarm_request is not None:
        source = run_swarm_turn(
            swarm_request, history=history, session_id=session_id,
            coordinator_id=agent_id, origin=origin, initial_plan=swarm_plan,
        )
    else:
        from app.runtime.tools import start_swarm
        from app.runtime.tools.registry import get_openai_tools

        solo_tools = [
            schema for schema in store.get_agent_openai_tools(agent_id)
            if schema.get("function", {}).get("name") not in {"delegate", "start_swarm"}
        ]
        if origin is None:
            solo_tools += get_openai_tools(["start_swarm"])

        async def chat_events():
            current_request = user_message or _last_user_content(history)
            token = start_swarm.bind_context(current_request) if origin is None else None
            solo = _agent_run_turn(
                user_message, history=history, agent_id=agent_id,
                session_id=session_id, origin=origin, tools=solo_tools,
            )
            handoff = None
            try:
                async for event in solo:
                    if event.get("kind") == "swarm_requested":
                        handoff = event["request"]
                        break
                    yield event
            finally:
                await solo.aclose()
                if token is not None:
                    start_swarm.reset_context(token)
            if handoff:
                async for event in run_swarm_turn(
                    handoff, history=history, session_id=session_id,
                    coordinator_id=agent_id, origin=origin,
                ):
                    yield event

        source = chat_events()
    async for ev in source:
        _accumulate_turn_tokens(token_acc, ev)
        # Nested subagent events carry their own agent_id for attribution.
        ev_agent_id = ev.get("agent_id") or agent_id
        if ev_agent_id != agent_id:
            ev_agent_name = ev.get("agent_name") or _agent_label(ev_agent_id)
            if ev_agent_id not in busy_ids:
                store.set_busy(ev_agent_id, True, session_id=session_id)
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
        # Persist before yield so a mid-turn disconnect still leaves history
        # durable for refresh/resume. History failures are logged and never
        # block the live wire (UI must keep streaming tools/ui events).
        for entry in entries:
            try:
                store.append_session_history(session_id, entry)
            except Exception:
                logger.exception(
                    "history append failed session_id=%s type=%s",
                    session_id,
                    entry.get("type"),
                )
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
    llm_title = await generate_session_title(
        pair[0], pair[1], agent_id=session.get("coordinator_id")
    )
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
    *,
    execution_mode: str = "solo",
    acquire_lock: bool = True,
    origin: str | None = None,
) -> AsyncIterator[str]:
    """Run one session turn and yield SSE chunks, persisting history.

    Supports coordinator turns, ``@mention`` force-handoff to session members,
    and mid-turn ``delegate`` tool handoffs with nested member ``run_turn``.

    Only one turn may run per session at a time; concurrent streams get an
    immediate error so the client can re-queue the message.

    When the caller already holds the session-turn lease (background
    ``start_session_turn``), pass ``acquire_lock=False`` so lock ownership
    stays with that single lease owner.
    """
    seq = start_seq
    busy_ids: set[str] = set()
    ctx_token = None
    wp_tokens = None
    primary_agent = coordinator_id
    turn_locked = False
    # True once this generator actually runs a turn (not a busy reject).
    # Background callers pass acquire_lock=False but still need Token Monitor.
    should_dispatch_turn_end = False
    # Cumulative prompt/completion tokens for Token Monitor (all agents this turn).
    token_acc: dict[str, int] = {"prompt": 0, "completion": 0}
    if acquire_lock:
        turn_locked = store.try_begin_session_turn(session_id)
        if not turn_locked:
            logger.warning(
                "turn rejected session_id=%s reason=session busy concurrent turn",
                session_id,
            )
            seq += 1
            yield session_busy_sse(
                agent_id=coordinator_id, session_id=session_id, seq=seq
            )
            return
        should_dispatch_turn_end = True
    else:
        # Caller holds the lease for the full background turn lifetime.
        turn_locked = False
        should_dispatch_turn_end = True

    logger.info(
        "turn begin session_id=%s coordinator_id=%s start_seq=%s message=%r",
        session_id,
        coordinator_id,
        start_seq,
        (message or "")[:120],
    )
    try:
        from app.runtime.permissions.slash import handle_approval_slash

        slash_notice = handle_approval_slash(message or "", session_id)
        if slash_notice is not None:
            store.append_session_history(
                session_id,
                {
                    "type": "user",
                    "content": (message or "").strip(),
                    "agent_id": coordinator_id,
                    "ts": now(),
                },
            )
            store.append_session_history(
                session_id,
                {
                    "type": "final",
                    "content": slash_notice,
                    "agent_id": coordinator_id,
                    "ts": now(),
                },
            )
            seq += 1
            yield fmt_sse(
                {
                    "event": "turn.start",
                    "data": {
                        "turn_id": "slash",
                        "agent": _agent_label(coordinator_id),
                        "agent_id": coordinator_id,
                    },
                    "seq": seq,
                }
            )
            seq += 1
            yield fmt_sse(
                {
                    "event": "delta",
                    "data": {
                        "content": slash_notice,
                        "agent_id": coordinator_id,
                        "agent": _agent_label(coordinator_id),
                    },
                    "seq": seq,
                }
            )
            seq += 1
            from app.runtime.permissions.modes import mode_payload

            approval = mode_payload(session_id)
            yield fmt_sse(
                {
                    "event": "done",
                    "data": {
                        "content": slash_notice,
                        "agent_id": coordinator_id,
                        "agent": _agent_label(coordinator_id),
                        "turn_id": "slash",
                        "approval": approval,
                    },
                    "seq": seq,
                }
            )
            seq += 1
            yield fmt_sse(
                {
                    "event": "turn.end",
                    "data": {"approval": approval},
                    "seq": seq,
                }
            )
            return

        session = store.get_session(session_id) or {}
        member_ids, member_agents = _session_agents(session)
        use_swarm = execution_mode == "swarm" or (message or "").strip().startswith("/swarm ")
        swarm_request = (message or "").strip()[7:].strip() if (message or "").strip().startswith("/swarm ") else message
        # A bare "swarm" / "pakai swarm" names no task: it asks for a team on the
        # previous request. Without this the solo agent tries to swarm itself.
        if _is_bare_swarm_optin(message):
            previous = _previous_user_request(session_id)
            if previous:
                use_swarm, swarm_request = True, previous
        solo_request: str | None = None
        approved_plan: dict[str, Any] | None = None
        from app.models.mixins import swarm as swarm_store

        pending = store.with_db(lambda conn: swarm_store.get_proposal(conn, session_id))
        answer = (message or "").strip().casefold().strip(".! ")
        if pending and answer in {"gas", "ya", "iya", "yes", "go", "go ahead", "lanjut", "setuju"}:
            use_swarm = True
            swarm_request = pending["request"]
            approved_plan = pending["plan"]
            store.with_db(lambda conn: swarm_store.clear_proposal(conn, session_id))
        elif pending and answer in {"tidak", "nggak", "enggak", "no", "gak", "ga", "solo", "kerjakan sendiri"}:
            solo_request = pending["request"]
            use_swarm = False
            store.with_db(lambda conn: swarm_store.clear_proposal(conn, session_id))
        elif pending:
            store.with_db(lambda conn: swarm_store.clear_proposal(conn, session_id))
        # Membership controls direct @mentions; it is not an execution mode.
        # A normal turn cannot silently delegate to the whole stored roster.
        routable_ids, routable_agents = member_ids, member_agents
        if not use_swarm:
            member_ids = [coordinator_id]
            member_agents = [a for a in member_agents if a.get("id") == coordinator_id]
        ctx_token = delegate_tool.bind_context(
            agent_ids=member_ids, agents=member_agents
        )

        mention, mention_rest = parse_leading_mention(message)
        force_target: str | None = None
        if mention:
            force_target = resolve_target(
                agent_ids=routable_ids, agents=routable_agents, query=mention
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

        store.set_busy(coordinator_id, True, session_id=session_id)
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
            from app.services.chat import attachment_meta_for_ids

            # ChatGPT-style: store clean user text + attachment chips metadata.
            # File contents are expanded only when building the LLM prompt.
            clean = (message or "").strip()
            advice = None
            if not use_swarm and not force_target and not solo_request and origin is None and not clean.startswith("/"):
                advice = await advise_swarm(
                    clean, session_id=session_id, coordinator_id=coordinator_id,
                    history=store.get_session_history(session_id),
                )
                if advice and advice[0] == "run":
                    use_swarm, swarm_request = True, clean
                    # The runtime plans and validates workers after routing.
                    approved_plan = None
            user_entry: dict = {"type": "user", "content": clean, "ts": now()}
            if use_swarm:
                user_entry["execution_mode"] = "swarm"
            if attachment_ids:
                meta = attachment_meta_for_ids(attachment_ids)
                user_entry["attachment_ids"] = list(attachment_ids)
                user_entry["attachments"] = meta
                if not clean and meta:
                    # Empty caption — keep content blank; UI shows chips only.
                    user_entry["content"] = ""
            new_title = store.append_session_history(session_id, user_entry)
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

            if advice and advice[0] == "propose":
                _, plan, summary = advice
                store.with_db(lambda conn: swarm_store.set_proposal(
                    conn, session_id, clean, plan, summary,
                ))
                chunks, entries, seq = map_loop_event(
                    {"kind": "final", "content": summary}, coordinator_id,
                    _agent_label(coordinator_id), seq, turn_id,
                )
                for entry in entries:
                    store.append_session_history(session_id, entry)
                for chunk in chunks:
                    yield chunk
                return

            if force_target:
                logger.info(
                    "mention handoff session_id=%s from=%s to=%s",
                    session_id,
                    coordinator_id,
                    force_target,
                )
                primary_agent = force_target
                async for chunk, seq in _emit_delegate(
                    session_id,
                    from_id=coordinator_id,
                    to_id=force_target,
                    reason="mention",
                    seq=seq,
                ):
                    yield chunk
                store.set_busy(coordinator_id, False, session_id=session_id)
                # Persist full ``@ops …`` user row; feed the member the stripped
                # prompt without the user row or the just-written handoff row.
                hist = store.get_session_history(session_id)
                hist_for_member = _history_before_last_user(hist)
                from app.services.chat import expand_slash_skill, prepend_attachment_info

                # Caption for the member (no @mention); expand slash skills +
                # files only in this ephemeral LLM prompt — history UI stays clean.
                member_prompt = prepend_attachment_info(
                    expand_slash_skill(mention_rest.strip() or message), attachment_ids
                )
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
                    token_acc=token_acc,
                    origin=origin,
                ):
                    yield chunk
            else:
                async for chunk, seq in _drain_agent_turn(
                    session_id,
                    coordinator_id,
                    user_message=solo_request,
                    history=(_history_before_last_user(store.get_session_history(session_id))
                             if solo_request else store.get_session_history(session_id)),
                    seq=seq,
                    turn_id=turn_id,
                    busy_ids=busy_ids,
                    token_acc=token_acc,
                    origin=origin,
                    swarm_request=(swarm_request or clean) if use_swarm else None,
                    swarm_plan=approved_plan,
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
            store.set_busy(aid, False, session_id=session_id)
        store.set_busy(coordinator_id, False, session_id=session_id)
        if turn_locked:
            store.end_session_turn(session_id)
        if should_dispatch_turn_end:
            try:
                store.dispatch_turn_end(
                    session_id=session_id,
                    agent_id=primary_agent,
                    message=(message or "").strip(),
                    prompt_tokens=int(token_acc.get("prompt") or 0),
                    completion_tokens=int(token_acc.get("completion") or 0),
                )
            except Exception:
                logger.exception(
                    "turn_end dispatch failed session_id=%s", session_id
                )

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
