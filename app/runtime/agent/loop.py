"""LLM turn loop and tool execution.

Orchestration only — no HTTP, no SSE formatting, no persistence. The loop
calls an :class:`~app.runtime.llm.base.LLMClient` with the OpenAI tool
schemas from the registry, executes any requested tools via the registry,
and yields a stream of internal ``dict`` events for the chat layer to map
onto SSE:

* ``{"kind": "thinking", "content": str}``          # optional reasoning
* ``{"kind": "delta", "content": str}``             # streamed text token/chunk
* ``{"kind": "tool", "tool": str, "args": dict, "call_id": str}``
* ``{"kind": "tool_result", "tool": str, "result": str, "error": bool, "call_id": str}``
* ``{"kind": "ui", "ui_id": str, "mode": str, "tree": dict}``
* ``{"kind": "delegate", "from": str, "to": str, "reason": str,
   "task": str, "parallel_index": int, "parallel_total": int}``
* ``{"kind": "subagent_start", "agent_id": str, "task": str,
   "parallel_index": int, "parallel_total": int}``
* ``{"kind": "subagent_done", "agent_id": str, "content": str,
   "status": "ok" | "error"}``
* ``{"kind": "todos", "todos": list, "source": "atg"|"tool"}``  # plan checklist
* ``{"kind": "error", "message": str}``

**Subagent delegation.** When the model calls ``delegate``, the target agent
runs a nested ``run_turn`` whose events are re-emitted (tagged with the
target ``agent_id``). The child's final output becomes the ``delegate`` tool
result, and the **parent loop continues** reasoning over it — it does not
stop after delegating. Depth is capped at :data:`MAX_DELEGATE_DEPTH`.

**Loop detection.** A sliding window of ``(tool, args)`` signatures stops
repeated identical tool calls (window 10, threshold 5) by injecting a
force-stop — preventing token-burn spin loops.

**Parallel read-only tools.** When a round carries multiple independent
read-only tool calls, they run concurrently via :func:`asyncio.gather`;
mutating tools run serially. Events are emitted in original call order.

**Planning.** Session planning is prompt-gated via the ``todo`` tool (the
model decides when to track multi-step work). Optional ATG: pass
``enable_atg=True`` to front-load a compiled DAG that seeds the same
checklist; default is off (no word/length heuristic).

**Active learning.** After eligible top-level finals, a background review may
distill durable facts (``memory`` / ``remember``) and procedural skills
(``manage_skill``). Triggers: every N turns (memory) and cumulative tool
iters / skill-touch (skills). Mid-turn the agent can also call those tools
directly.
"""
from __future__ import annotations

import asyncio
import collections
import itertools
import json
import logging
import re
from typing import Any, AsyncIterator

from app.runtime.agent.compress import maybe_compress_messages
from app.runtime.agent.context import (
    build_messages,
    build_system_prompt,
    freeze_prompt_clock,
    reset_prompt_clock,
)
from app.runtime.agent.metrics import TurnMetrics
from app.runtime.agent.retry import is_transient_llm_error
from app.runtime.agent.subagent import (
    MAX_TOOL_RESULT_CHARS,
    current_depth,
    depth_exceeded,
    drain_subagent_turn,
)
from app.runtime.agent.tool_errors import tool_result_is_error
from app.runtime.llm import get_llm
from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.permissions.gate import Decision, apply_choice, evaluate
from app.runtime.permissions.grants import reset_outside_grant, set_outside_grant
from app.runtime.permissions import hitl as hitl_mod
from app.runtime.permissions.modes import get_effective_mode
from app.runtime.permissions.smart import command_from_args, smart_approve
from app.runtime.tools import sandbox
from app.runtime.tools.delegate import parse_delegated_id
from app.runtime.tools.registry import execute_async, get_openai_tools

_logger = logging.getLogger(__name__)

# Loop-detection tuning (sliding window of tool+args signatures).
_LOOP_WINDOW = 10
_LOOP_THRESHOLD = 5
_MAX_UI_RESULT_CHARS = 64_000

# These tools already expose pagination/continuation contracts. Give their
# complete pages room to reach the UI/model, while keeping the default cap for
# unbounded command or delegated output.
_PAGINATED_RESULT_LIMITS = {
    "list_skills": 12_000,
    "read_file": 16_000,
    "web_fetch": 16_000,
    "use_skill": 16_000,
}
_CONTINUATION_RE = re.compile(
    r"Continue with offset=\d+(?:\s+\(limit=\d+\))?\.?", re.IGNORECASE
)

# Read-only tools safe to run in parallel within one round (after gating).
_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "search_files",
        "web_fetch",
        "web_search",
        "recall",
        "render_ui",
        "session_search",
        "list_skills",
        "list_workplaces",
        "todo",
        "use_skill",
        "agent_state",
        "recall",
    }
)


def _max_tool_iterations() -> int:
    from app.services import store

    try:
        raw = store.get_settings().get("max_tool_iterations", 12)
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 12


def _should_run_atg(goal: str, *, enable_atg: bool | None) -> bool:
    """Front-load ATG only when explicitly requested.

    Default product path relies on the ``todo`` tool (prompt-gated). Nested
    subagent turns never run ATG (parent owns the plan).
    """
    if enable_atg is not True:
        return False
    if not (goal or "").strip():
        return False
    # Nested delegates must not re-compile a DAG on every handoff.
    if current_depth() > 0:
        return False
    from app.runtime.agent.atg import is_atg_eligible

    return is_atg_eligible(enable_atg=True)

def _estimate_round_usage(
    messages: list[dict[str, Any]], resp: LLMResponse
) -> tuple[int, int]:
    """Rough in/out token estimate when the provider omitted ``usage``."""
    from app.runtime.agent.context_usage import estimate_tokens

    prompt_parts: list[str] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str) and content:
            prompt_parts.append(content)
        elif content is not None:
            prompt_parts.append(str(content))
        if msg.get("tool_calls"):
            prompt_parts.append(str(msg["tool_calls"]))
    prompt = estimate_tokens("\n".join(prompt_parts)) if prompt_parts else 0

    out_parts: list[str] = []
    if resp.content:
        out_parts.append(resp.content)
    for call in resp.tool_calls or []:
        out_parts.append(f"{call.name} {call.arguments!s}")
    completion = estimate_tokens("\n".join(out_parts)) if out_parts else 0
    return prompt, completion


def _record_response_usage(
    metrics: TurnMetrics | None,
    messages: list[dict[str, Any]],
    resp: LLMResponse,
) -> None:
    """Accumulate prompt/completion tokens for one LLM round onto *metrics*."""
    if metrics is None:
        return
    prompt = int(getattr(resp, "prompt_tokens", 0) or 0)
    completion = int(getattr(resp, "completion_tokens", 0) or 0)
    if prompt <= 0 and completion <= 0:
        prompt, completion = _estimate_round_usage(messages, resp)
    metrics.add_usage(prompt, completion)


async def _llm_round(
    client: LLMClient,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Run one LLM round, preferring token streaming when available.

    Yields ``{"kind": "delta", ...}`` chunks, then
    ``{"kind": "_response", "response": LLMResponse}``.
    """
    stream_fn = getattr(client, "stream_complete", None)
    if stream_fn is None:
        resp = await client.complete(messages, tool_schemas)
        if resp.content and not resp.has_tool_calls:
            yield {"kind": "delta", "content": resp.content}
        yield {"kind": "_response", "response": resp}
        return

    async for ev in stream_fn(messages, tool_schemas):
        if ev.get("type") == "delta" and ev.get("content"):
            yield {"kind": "delta", "content": ev["content"]}
        elif ev.get("type") == "done":
            yield {"kind": "_response", "response": ev["response"]}


async def _llm_round_with_retry(
    client: LLMClient,
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]],
    *,
    metrics: TurnMetrics | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Like ``_llm_round`` but retries the whole round on transient failures.

    Forwards each piece to the caller as soon as ``_llm_round`` produces it —
    real provider token-streaming must reach the SSE layer immediately, not
    only after the whole round finishes (buffering the full round here turns
    live streaming into one long wait followed by the whole reply appearing
    at once). Retry only kicks in for a failure that happens before any
    piece has reached the caller — once a delta has been forwarded it cannot
    be un-sent, so a failure past that point propagates instead of silently
    restarting the round.
    """
    last_exc: BaseException | None = None
    for attempt in range(2):
        forwarded = False
        try:
            async for piece in _llm_round(client, messages, tool_schemas):
                forwarded = True
                yield piece
            return
        except Exception as exc:
            last_exc = exc
            if attempt == 0 and not forwarded and is_transient_llm_error(exc):
                if metrics is not None:
                    metrics.llm_retries += 1
                _logger.warning("LLM round transient failure — retrying: %s", exc)
                await asyncio.sleep(0.75)
                continue
            raise
    if last_exc is not None:
        raise last_exc



def _peek_has_steers(session_id: str | None) -> bool:
    if not session_id:
        return False
    try:
        from app.services.chat import get_active_session_turn

        turn = get_active_session_turn(session_id)
        if turn is None:
            return False
        with turn._steer_lock:
            return bool(turn.steer_inbox)
    except Exception:
        return False


async def _emit_drained_steers(
    messages: list[dict[str, Any]], session_id: str | None
) -> AsyncIterator[dict[str, Any]]:
    """Drain mid-turn steers into *messages* and yield ``steer`` events."""
    if not session_id:
        return
    try:
        from app.services.chat import (
            attachment_meta_for_ids,
            drain_session_steers,
            expand_user_content_for_llm,
        )
    except Exception:
        return
    items = drain_session_steers(session_id)
    for item in items:
        clean = str(item.get("content") or "")
        ids = list(item.get("attachment_ids") or [])
        entry = {"content": clean, "attachment_ids": ids}
        llm_text = expand_user_content_for_llm(entry)
        messages.append({"role": "user", "content": llm_text or clean or "(attachment)"})
        yield {
            "kind": "steer",
            "content": clean,
            "attachment_ids": ids,
            "attachments": attachment_meta_for_ids(ids),
        }


def _last_user_text(
    user_message: str | None, history: list[dict[str, Any]] | None
) -> str:
    """Extract the originating user request for subagent task prompts."""
    if user_message:
        return user_message
    if history:
        for entry in reversed(history):
            if entry.get("type") == "user":
                return str(entry.get("content") or "")
    return ""


# Back-compat alias for tests that patch/import the old name.
_tool_result_is_error = tool_result_is_error


async def _handle_clarify(
    call: ToolCall,
    *,
    session_id: str | None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield clarify HITL events then a tool_result."""
    args = call.arguments if isinstance(call.arguments, dict) else {}
    question = args.get("question")
    if not isinstance(question, str) or not question.strip():
        yield {
            "kind": "tool_result",
            "tool": call.name,
            "result": "Error: 'question' argument must be a non-empty string",
            "error": True,
        }
        return
    raw_choices = args.get("choices")
    choices: list[str] = []
    if isinstance(raw_choices, list):
        for c in raw_choices:
            if isinstance(c, str) and c.strip() and len(choices) < 4:
                choices.append(c.strip())
    payload = hitl_mod.create_clarify(
        question=question.strip(),
        choices=choices,
        session_id=session_id,
    )
    yield {
        "kind": "clarify_required",
        **{k: v for k, v in payload.items() if k != "kind"},
    }
    answer = await hitl_mod.await_clarify(payload["id"])
    result = json.dumps(
        {
            "question": question.strip(),
            "choices_offered": choices,
            "user_response": answer,
        },
        ensure_ascii=False,
    )
    yield {
        "kind": "tool_result",
        "tool": call.name,
        "result": result,
        "error": False,
    }


async def _authorize_tool(
    call: ToolCall,
    *,
    session_id: str | None,
    origin: str | None = None,
) -> AsyncIterator[dict[str, Any] | Decision]:
    """Yield HITL events; finally yield a :class:`Decision` or blocked result dict.

    The last yielded value is either a ``Decision`` (allowed or not) or a
    finished ``tool_result`` dict (clarify path / early error).

    Nested delegate turns never prompt for tool approval — ``evaluate``
    already auto-allows when ``current_depth() > 0`` (hardline / user_deny
    still block). Session ``/auto``/``/smart`` still apply at the top level.

    Scheduler fires (``origin == "scheduler"``) never HITL: they evaluate in
    ``off``-like mode so approvals are bypassed while hardline / user_deny
    still block, then skip the smart/HITL waiters entirely.
    """
    args = call.arguments if isinstance(call.arguments, dict) else {}
    work_root = sandbox.resolve_work_root()
    is_scheduler = origin == "scheduler"
    decision = evaluate(
        call.name,
        args,
        work_root=work_root,
        session_id=session_id,
        mode_override="off" if is_scheduler else None,
    )

    # Belt-and-suspenders if evaluate was called without nested depth set.
    if decision.needs_hitl and current_depth() > 0:
        decision = apply_choice(decision, "once", session_id=session_id)
        decision.grant = "*"
        yield decision
        return

    # Mid-turn override to Auto (off) after evaluate() — honor immediately.
    if decision.needs_hitl and get_effective_mode(session_id) == "off":
        decision = apply_choice(decision, "once", session_id=session_id)
        yield decision
        return

    if decision.needs_hitl and get_effective_mode(session_id) == "smart":
        cmd = command_from_args(call.name, args)
        verdict = await smart_approve(
            cmd or decision.description, decision.description
        )
        if verdict == "approve":
            decision = apply_choice(decision, "once", session_id=session_id)
        elif verdict == "deny":
            decision.smart_denied = True
            decision.allow_permanent = False

    # Re-check Auto after smart (user may have flipped mode mid-assessment).
    if decision.needs_hitl and get_effective_mode(session_id) == "off":
        decision = apply_choice(decision, "once", session_id=session_id)
        yield decision
        return

    if decision.needs_hitl:
        payload = hitl_mod.create_approval(
            tool=call.name,
            args=args,
            findings=decision.findings,
            description=decision.description,
            allow_permanent=decision.allow_permanent,
            smart_denied=decision.smart_denied,
            session_id=session_id,
        )
        yield {
            "kind": "approval_required",
            **{k: v for k, v in payload.items() if k != "kind"},
        }
        choice = await hitl_mod.await_approval(payload["id"])
        decision = apply_choice(decision, choice, session_id=session_id)

    yield decision


async def _execute_authorized(call: ToolCall, decision: Decision) -> str:
    """Run a tool under the granted outside-jail token."""
    args = call.arguments if isinstance(call.arguments, dict) else {}
    grant_tok = set_outside_grant(decision.grant)
    try:
        return await execute_async(call.name, args)
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        reset_outside_grant(grant_tok)


async def _run_one_gated_tool(
    call: ToolCall,
    *,
    session_id: str | None,
    call_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield optional HITL events, then a ``tool_result`` event (serial path)."""
    cid = call_id or getattr(call, "id", None) or ""
    if call.name == "clarify":
        async for ev in _handle_clarify(call, session_id=session_id):
            if ev.get("kind") == "tool_result" and cid:
                ev = {**ev, "call_id": cid}
            yield ev
        return

    decision: Decision | None = None
    async for item in _authorize_tool(call, session_id=session_id):
        if isinstance(item, Decision):
            decision = item
        else:
            yield item
    assert decision is not None
    if not decision.allowed:
        result = decision.message or "BLOCKED: denied"
        payload = {
            "kind": "tool_result",
            "tool": call.name,
            "result": result,
            "error": True,
        }
        if cid:
            payload["call_id"] = cid
        yield payload
        return

    result = _truncate_result(
        await _execute_authorized(call, decision), tool_name=call.name
    )
    payload = {
        "kind": "tool_result",
        "tool": call.name,
        "result": result,
        "error": tool_result_is_error(result),
    }
    if cid:
        payload["call_id"] = cid
    yield payload


def _truncate_result(result: Any, *, tool_name: str | None = None) -> str:
    """Truncate oversized tool results to bound the context window."""
    raw = result if isinstance(result, str) else str(result or "")
    if tool_name == "bash":
        # Bash applies its own 100k output safety cap. Preserve that complete
        # result here; shell output has no offset/continuation contract.
        return raw
    if tool_name == "render_ui":
        limit = _MAX_UI_RESULT_CHARS
    else:
        limit = _PAGINATED_RESULT_LIMITS.get(tool_name, MAX_TOOL_RESULT_CHARS)
    if len(raw) <= limit:
        return raw

    continuation = _CONTINUATION_RE.findall(raw)
    if continuation:
        hint = continuation[-1]
        suffix = f"\n\n… page shortened for context. {hint}"
        body_limit = max(0, limit - len(suffix))
        return raw[:body_limit].rstrip() + suffix
    return raw[:limit] + f"\n…[truncated, {len(raw)} chars]"


def _call_signature(call: ToolCall) -> str:
    """Stable signature for loop detection (tool name + sorted args)."""
    try:
        return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
    except (TypeError, ValueError):
        return f"{call.name}:{call.arguments}"


def _with_ids(
    tool_calls: list[ToolCall], counter: itertools.count
) -> list[tuple[str, ToolCall]]:
    """Pair each tool call with a non-empty id, synthesising one if missing."""
    out: list[tuple[str, ToolCall]] = []
    for call in tool_calls:
        cid = call.id or f"call_{next(counter)}"
        out.append((cid, call))
    return out


def _assistant_tool_calls_message(
    resp: LLMResponse, paired: list[tuple[str, ToolCall]]
) -> dict[str, Any]:
    """Build the OpenAI assistant message carrying this round's tool calls."""
    return {
        "role": "assistant",
        "content": resp.content,
        "tool_calls": [
            {
                "id": cid,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for cid, call in paired
        ],
    }


async def _maybe_run_atg(
    *,
    user_message: str | None,
    tools: list[dict[str, Any]],
    llm: LLMClient,
    agent_id: str | None,
    session_id: str | None = None,
) -> AsyncIterator[dict[str, Any]] | None:
    """Compile + execute a task DAG; seed the session todo checklist from it.

    Yields ATG events (tool/tool_result/atg_wave/atg_summary/todos). Returns
    None when compile fails. The final ``atg_summary`` event's ``summary`` is
    what the loop feeds back.
    """
    goal = (user_message or "").strip()
    if not goal:
        return None
    try:
        from app.runtime.agent.atg import compile_task_graph, run_dag_execution
        from app.runtime.tools import todo as todo_mod

        dag, _history = await compile_task_graph(goal, tools, llm)
        # Visible plan: ATG nodes become the session todo list.
        snap = todo_mod.seed_from_dag(dag, session_id=session_id)

        async def _gen():
            yield {
                "kind": "todos",
                "todos": snap.get("todos") or [],
                "summary": snap.get("summary") or {},
                "source": "atg",
                "agent_id": agent_id or "",
            }
            async for ev in run_dag_execution(
                dag, llm=llm, tools=tools, agent_id=agent_id
            ):
                # Promote per-node todo snapshots to a dedicated todos event too.
                if ev.get("kind") == "tool_result" and ev.get("todos") is not None:
                    yield {
                        "kind": "todos",
                        "todos": ev["todos"],
                        "source": "atg",
                        "agent_id": agent_id or "",
                        "atg_node": ev.get("atg_node"),
                    }
                yield ev

        return _gen()
    except Exception as exc:
        _logger.warning("ATG compile failed: %s", exc)
        return None


async def run_turn(
    user_message: str | None,
    *,
    history: list[dict[str, Any]] | None = None,
    llm: LLMClient | None = None,
    tools: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    max_iterations: int | None = None,
    enable_atg: bool | None = None,
    origin: str | None = None,
    reasoning_effort: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one agent turn, yielding internal events.

    ``user_message`` is the new turn input; pass ``None`` when the caller has
    already persisted the user entry into ``history`` so it is not
    duplicated. ``llm`` / ``tools`` / ``system_prompt`` / ``max_iterations``
    default to settings-backed values but are injectable for tests.
    ``agent_id`` selects the per-agent ``SYSTEM.md`` / ``SOUL.md``;
    ``session_id`` is context for persistence wiring and the session todo
    store. Planning defaults to the prompt-gated ``todo`` tool. Pass
    ``enable_atg=True`` to front-load an ATG DAG that seeds the same
    checklist; omit or pass ``False`` to leave ATG off.

    Setup failures and per-round backend failures are surfaced as
    ``{"kind": "error", ...}`` events — ``run_turn`` never raises out to the
    consumer. The function is an async generator — iterate with ``async for``.
    """
    from app.runtime.tools import todo as todo_mod

    metrics = TurnMetrics(agent_id=agent_id, session_id=session_id)
    sandbox_token = sandbox.bind_agent(agent_id)
    todo_token = todo_mod.bind_session(session_id)
    from app.runtime.artifacts import fs as artifacts_fs
    from app.runtime.tools import user_ctx as user_ctx_mod

    arts_token = artifacts_fs.bind_session(session_id)
    # Bind session owner so knowledge / session_search / USER.md stay private.
    turn_user_id = "web"
    if session_id:
        try:
            from app.services import store as _store_for_uid

            _sess = _store_for_uid.get_session(session_id)
            if _sess:
                turn_user_id = (_sess.get("user_id") or "web").strip() or "web"
        except Exception:
            turn_user_id = "web"
    user_token = user_ctx_mod.bind_user(turn_user_id)
    # Stable system-prompt clock for this turn (hour precision + freeze).
    clock_token = freeze_prompt_clock()
    skills_touched: list[str] = []
    try:
        try:
            if llm is not None:
                client = llm
            else:
                selected_effort = reasoning_effort
                if selected_effort is None and session_id:
                    from app.services import store

                    selected_effort = store.resolve_session_reasoning_effort(
                        session_id, agent_id
                    )
                if selected_effort is None:
                    client = get_llm(agent_id)
                else:
                    client = get_llm(agent_id, reasoning_effort=selected_effort)
            if tools is not None:
                tool_schemas = tools
            elif agent_id:
                from app.services import store

                from app.runtime.mcp import mcp_manager

                connected = await mcp_manager.ensure_for_servers(
                    store.list_mcp_server_ids_for_agent(agent_id)
                )
                tool_schemas = store.get_agent_openai_tools(
                    agent_id, connected_server_ids=connected
                )
            else:
                tool_schemas = get_openai_tools()
            limit = (
                max_iterations
                if max_iterations is not None
                else _max_tool_iterations()
            )
            prompt = system_prompt
            if prompt is None:
                prompt = build_system_prompt(agent_id, session_id=session_id)
            from app.runtime.llm.vision import agent_supports_vision

            vision_capable = agent_supports_vision(agent_id)
            messages = build_messages(
                history,
                user_message,
                system_prompt=prompt,
                for_agent_id=agent_id,
                session_id=session_id,
                vision_capable=vision_capable,
            )
        except Exception as exc:
            metrics.ended_kind = "error"
            metrics.log_summary()
            yield {"kind": "error", "message": f"Agent setup failed: {exc}"}
            return

        use_atg = _should_run_atg(user_message or "", enable_atg=enable_atg)

        _logger.info(
            "turn start agent=%s session=%s tools=%d limit=%d atg=%s",
            agent_id,
            session_id,
            len(tool_schemas),
            limit,
            use_atg,
        )

        id_counter = itertools.count()
        user_request = _last_user_text(user_message, history)

        # ── ATG branch: front-load a compiled DAG before the tool loop ──
        if use_atg:
            metrics.atg_used = True
            atg_gen = await _maybe_run_atg(
                user_message=user_message,
                tools=tool_schemas,
                llm=client,
                agent_id=agent_id,
                session_id=session_id,
            )
            if atg_gen is not None:
                atg_summary = ""
                async for ev in atg_gen:
                    ev = dict(ev)
                    ev.setdefault("agent_id", agent_id or "")
                    yield ev
                    if ev["kind"] == "atg_summary":
                        atg_summary = ev.get("summary") or ""
                        metrics.atg_status = ev.get("status")
                if atg_summary:
                    messages.append(
                        {"role": "user", "content": f"[SYSTEM] {atg_summary}"}
                    )
            else:
                metrics.atg_status = "compile_failed"

        # ── Loop-detection state ──
        call_window: collections.deque[str] = collections.deque(maxlen=_LOOP_WINDOW)
        force_stopped = False

        iteration = 0
        while iteration < limit:
            iteration += 1
            # Mid-turn steers (composer queue → Enter / ctrl+s).
            async for steer_ev in _emit_drained_steers(messages, session_id):
                yield steer_ev
            resp: LLMResponse | None = None
            streamed = False
            metrics.mark_llm_round()
            before_len = len(messages)
            compressed = maybe_compress_messages(messages)
            if compressed is not messages:
                messages = compressed
                metrics.compressed = True
            _logger.info("LLM round %d agent=%s msgs=%d…", iteration, agent_id, len(messages))
            try:
                async for piece in _llm_round_with_retry(
                    client, messages, tool_schemas, metrics=metrics
                ):
                    if piece["kind"] == "delta":
                        streamed = True
                        yield piece
                    elif piece["kind"] == "_response":
                        resp = piece["response"]
            except Exception as exc:
                metrics.ended_kind = "error"
                metrics.log_summary()
                from app.runtime.llm.openai_compat import format_llm_error

                msg = format_llm_error(exc)
                _logger.exception("LLM round failed agent=%s: %s", agent_id, msg)
                yield {"kind": "error", "message": msg}
                return
            _ = before_len  # kept for readability / future delta metrics

            if resp is None:
                metrics.ended_kind = "error"
                metrics.log_summary()
                yield {
                    "kind": "error",
                    "message": "LLM stream ended without a response",
                }
                return

            _record_response_usage(metrics, messages, resp)

            if resp.has_tool_calls and resp.content:
                yield {"kind": "thinking", "content": resp.content}

            if not resp.has_tool_calls:
                # Late steer during the LLM round — keep going instead of ending.
                if _peek_has_steers(session_id):
                    final_content = resp.content or ""
                    if final_content:
                        messages.append(
                            {"role": "assistant", "content": final_content}
                        )
                        yield {
                            "kind": "final",
                            "content": final_content,
                            "already_streamed": streamed,
                            "continued": True,
                        }
                    async for steer_ev in _emit_drained_steers(messages, session_id):
                        yield steer_ev
                    continue

                _logger.info(
                    "turn end agent=%s final_chars=%d",
                    agent_id,
                    len(resp.content or ""),
                )
                metrics.ended_kind = "final"
                metrics.log_summary()
                final_content = resp.content or ""
                yield {
                    "kind": "final",
                    "content": final_content,
                    "already_streamed": streamed,
                    "metrics": metrics.as_dict(),
                }
                from app.runtime.agent.learning import schedule_learning_review

                schedule_learning_review(
                    client=client,
                    messages=messages,
                    metrics=metrics,
                    user_message=user_message,
                    final_content=final_content,
                    skills_touched=skills_touched,
                    nested=current_depth() > 0,
                )
                return

            paired = _with_ids(resp.tool_calls, id_counter)
            messages.append(_assistant_tool_calls_message(resp, paired))

            tool_names = [c.name for _cid, c in paired]
            _logger.info("tool round %d: %s", iteration, tool_names)
            for _cid, call in paired:
                if call.name not in {"use_skill", "manage_skill"}:
                    continue
                args = call.arguments if isinstance(call.arguments, dict) else {}
                sid = args.get("skill_id") or args.get("name") or args.get("id")
                if isinstance(sid, str) and sid.strip():
                    skills_touched.append(sid.strip())

            # ── Loop detection: identical (tool, args) repeated too often ──
            for _cid, call in paired:
                sig = _call_signature(call)
                call_window.append(sig)
                if call_window.count(sig) >= _LOOP_THRESHOLD and not force_stopped:
                    force_stopped = True
                    _logger.warning(
                        "loop detected agent=%s tool=%s sig=%s — forcing stop",
                        agent_id,
                        call.name,
                        sig[:80],
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "[SYSTEM] You are repeating the same tool call "
                                f"({call.name}) without progress. Stop calling "
                                "tools and give a final answer now."
                            ),
                        }
                    )

            # ── Tool execution: gate serially, run auto-allowed RO in parallel ──
            delegate_calls = [
                (cid, c) for cid, c in paired if c.name == "delegate"
            ]
            other_calls = [
                (cid, c) for cid, c in paired if c.name != "delegate"
            ]
            if delegate_calls or other_calls:
                _logger.info(
                    "exec: delegate=%d tools=%d nested=%d",
                    len(delegate_calls),
                    len(other_calls),
                    current_depth(),
                )

            # Results keyed by call id. ``already_yielded`` tracks early emits
            # (clarify / blocked) so we do not double-emit after parallel exec.
            result_by_cid: dict[str, tuple[str, bool]] = {}
            already_yielded: set[str] = set()
            pending_ro: list[tuple[str, ToolCall, Decision]] = []
            pending_mut: list[tuple[str, ToolCall, Decision]] = []

            for cid, call in other_calls:
                yield {
                    "kind": "tool",
                    "tool": call.name,
                    "args": call.arguments,
                    "call_id": cid,
                }

                if call.name == "clarify":
                    result_text = "Error: no tool result"
                    error = True
                    async for ev in _handle_clarify(call, session_id=session_id):
                        if ev.get("kind") == "clarify_required":
                            yield ev
                            continue
                        if ev.get("kind") == "tool_result":
                            result_text = _truncate_result(ev.get("result"))
                            error = bool(ev.get("error"))
                            yield {
                                "kind": "tool_result",
                                "tool": call.name,
                                "result": result_text,
                                "error": error,
                                "call_id": cid,
                            }
                    result_by_cid[cid] = (result_text, error)
                    already_yielded.add(cid)
                    continue

                decision: Decision | None = None
                async for item in _authorize_tool(
                    call, session_id=session_id, origin=origin
                ):
                    if isinstance(item, Decision):
                        decision = item
                    else:
                        yield item
                assert decision is not None
                if not decision.allowed:
                    result_text = decision.message or "BLOCKED: denied"
                    yield {
                        "kind": "tool_result",
                        "tool": call.name,
                        "result": result_text,
                        "error": True,
                        "call_id": cid,
                    }
                    result_by_cid[cid] = (result_text, True)
                    already_yielded.add(cid)
                    continue

                if call.name in _READ_ONLY_TOOLS:
                    pending_ro.append((cid, call, decision))
                else:
                    pending_mut.append((cid, call, decision))

            # Parallel execute auto-allowed read-only tools.
            if len(pending_ro) > 1:
                ro_results = await asyncio.gather(
                    *(_execute_authorized(c, d) for _cid, c, d in pending_ro),
                    return_exceptions=True,
                )
                for (cid, _call, _d), res in zip(pending_ro, ro_results):
                    if isinstance(res, Exception):
                        res = f"Error: {res}"
                    text_res = _truncate_result(res, tool_name=_call.name)
                    result_by_cid[cid] = (text_res, tool_result_is_error(text_res))
            else:
                for cid, call, decision in pending_ro:
                    text_res = _truncate_result(
                        await _execute_authorized(call, decision),
                        tool_name=call.name,
                    )
                    result_by_cid[cid] = (text_res, tool_result_is_error(text_res))

            # Mutating tools stay serial.
            for cid, call, decision in pending_mut:
                text_res = _truncate_result(
                    await _execute_authorized(call, decision),
                    tool_name=call.name,
                )
                result_by_cid[cid] = (text_res, tool_result_is_error(text_res))

            # Emit remaining tool_results in original call order; append all.
            errors_this_round = 0
            for cid, call in other_calls:
                text_res, err = result_by_cid.get(cid, ("Error: no tool result", True))
                if cid not in already_yielded:
                    payload = {
                        "kind": "tool_result",
                        "tool": call.name,
                        "call_id": cid,
                        "result": text_res,
                        "error": err,
                    }
                    if call.name == "todo" and not err:
                        todos = todo_mod.parse_todos_payload(text_res)
                        if todos is not None:
                            payload["todos"] = todos
                            yield {
                                "kind": "todos",
                                "todos": todos,
                                "source": "tool",
                                "agent_id": agent_id or "",
                            }
                    yield payload
                    if call.name == "render_ui" and not err:
                        from app.runtime.tools.render_ui import parse_result

                        ui_payload = parse_result(text_res)
                        if ui_payload is not None:
                            yield {"kind": "ui", **ui_payload}
                elif call.name == "todo" and not err:
                    # Already yielded (shouldn't happen for todo) — still sync UI.
                    todos = todo_mod.parse_todos_payload(text_res)
                    if todos is not None:
                        yield {
                            "kind": "todos",
                            "todos": todos,
                            "source": "tool",
                            "agent_id": agent_id or "",
                        }
                if err:
                    errors_this_round += 1
                messages.append(
                    {"role": "tool", "tool_call_id": cid, "content": text_res}
                )

            metrics.mark_tools(
                len(other_calls),
                errors=errors_this_round,
                parallel=len(pending_ro) if len(pending_ro) > 1 else 0,
            )

            # ── Subagent delegation (stream live; parallel merges via queue) ──
            parallel_total = len(delegate_calls)
            metrics.delegates += parallel_total

            if parallel_total > 1:
                merge_q: asyncio.Queue = asyncio.Queue()
                results_by_cid: dict[str, str] = {}

                async def _run_one(
                    _cid: str,
                    _call: ToolCall,
                    _idx: int,
                ) -> None:
                    box: list[str] = []
                    try:
                        async for ev in _stream_delegate_bundle(
                            cid=_cid,
                            call=_call,
                            agent_id=agent_id,
                            user_request=user_request,
                            parallel_index=_idx + 1,
                            parallel_total=parallel_total,
                            session_id=session_id,
                            result_out=box,
                        ):
                            await merge_q.put(("ev", ev))
                    except Exception as exc:
                        box.clear()
                        box.append(f"Error: subagent failed: {exc}")
                        await merge_q.put(
                            (
                                "ev",
                                {
                                    "kind": "tool_result",
                                    "tool": _call.name,
                                    "result": box[0],
                                    "error": True,
                                    "call_id": _cid,
                                },
                            )
                        )
                    finally:
                        results_by_cid[_cid] = (
                            box[0] if box else "Error: no output from subagent"
                        )
                        await merge_q.put(("done", _cid))

                tasks = [
                    asyncio.create_task(_run_one(cid, call, idx))
                    for idx, (cid, call) in enumerate(delegate_calls)
                ]
                finished = 0
                while finished < parallel_total:
                    kind, payload = await merge_q.get()
                    if kind == "ev":
                        yield payload
                    else:
                        finished += 1
                await asyncio.gather(*tasks, return_exceptions=True)
                for cid, _call in delegate_calls:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": cid,
                            "content": results_by_cid.get(
                                cid, "Error: no output from subagent"
                            ),
                        }
                    )
            else:
                for idx, (cid, call) in enumerate(delegate_calls):
                    box: list[str] = []
                    async for ev in _stream_delegate_bundle(
                        cid=cid,
                        call=call,
                        agent_id=agent_id,
                        user_request=user_request,
                        parallel_index=idx + 1,
                        parallel_total=max(parallel_total, 1),
                        session_id=session_id,
                        result_out=box,
                    ):
                        yield ev
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": cid,
                            "content": box[0]
                            if box
                            else "Error: no output from subagent",
                        }
                    )

            continue

        # Max iterations: force one final no-tools synthesis instead of hard error.
        _logger.warning(
            "max iterations exceeded: agent=%s limit=%d — forcing final",
            agent_id,
            limit,
        )
        metrics.force_final = True
        messages.append(
            {
                "role": "user",
                "content": (
                    "[SYSTEM] You have reached the maximum number of tool "
                    f"iterations ({limit}). Do NOT call any more tools. "
                    "Summarize what you know and give the best final answer now."
                ),
            }
        )
        try:
            resp_final: LLMResponse | None = None
            streamed_final = False
            async for piece in _llm_round_with_retry(
                client, messages, [], metrics=metrics
            ):
                if piece["kind"] == "delta":
                    streamed_final = True
                    yield piece
                elif piece["kind"] == "_response":
                    resp_final = piece["response"]
            if resp_final is not None:
                _record_response_usage(metrics, messages, resp_final)
                content = (resp_final.content or "").strip()
                if resp_final.has_tool_calls and not content:
                    content = (
                        f"Stopped after {limit} tool iterations without a clean "
                        "final answer."
                    )
                metrics.ended_kind = "final"
                metrics.log_summary()
                yield {
                    "kind": "final",
                    "content": content,
                    "already_streamed": streamed_final and not resp_final.has_tool_calls,
                    "metrics": metrics.as_dict(),
                }
                from app.runtime.agent.learning import schedule_learning_review

                schedule_learning_review(
                    client=client,
                    messages=messages,
                    metrics=metrics,
                    user_message=user_message,
                    final_content=content,
                    skills_touched=skills_touched,
                    nested=current_depth() > 0,
                )
                return
        except Exception as exc:
            metrics.ended_kind = "error"
            metrics.log_summary()
            yield {
                "kind": "error",
                "message": (
                    f"Reached max tool iterations ({limit}) and force-final "
                    f"failed: {exc}"
                ),
            }
            return

        metrics.ended_kind = "error"
        metrics.log_summary()
        yield {
            "kind": "error",
            "message": (
                f"Reached max tool iterations ({limit}) without a final answer."
            ),
        }
    finally:
        reset_prompt_clock(clock_token)
        artifacts_fs.reset_session(arts_token)
        todo_mod.reset_session(todo_token)
        sandbox.reset_agent(sandbox_token)
        user_ctx_mod.reset_user(user_token)


async def _stream_delegate_bundle(
    *,
    cid: str,
    call: ToolCall,
    agent_id: str | None,
    user_request: str,
    parallel_index: int,
    parallel_total: int,
    session_id: str | None = None,
    result_out: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield delegate / subagent events **live** (do not buffer until done).

    The final delegate ``tool_result`` is yielded last. When ``result_out`` is
    provided, the truncated result string is appended so callers can feed the
    parent LLM tool message without re-scanning the stream.
    """
    yield {
        "kind": "tool",
        "tool": call.name,
        "args": call.arguments,
        "call_id": cid,
    }
    args = call.arguments if isinstance(call.arguments, dict) else {}
    reason = args.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = "delegate"
    reason = reason.strip()

    delegate_result = await execute_async(call.name, call.arguments)
    target = parse_delegated_id(str(delegate_result))
    delegate_error = tool_result_is_error(delegate_result)
    sub_result = ""

    if target and not delegate_error:
        if depth_exceeded():
            sub_result = (
                f"Error: delegation depth limit reached — "
                f"cannot delegate further to {target}."
            )
            delegate_error = True
        else:
            yield {
                "kind": "delegate",
                "from": agent_id or "",
                "to": target,
                "reason": reason,
                "task": reason,
                "parallel_index": parallel_index,
                "parallel_total": parallel_total,
                "delegate_call_id": cid,
            }
            yield {
                "kind": "subagent_start",
                "agent_id": target,
                "from": agent_id or "",
                "task": reason,
                "parallel_index": parallel_index,
                "parallel_total": parallel_total,
                "delegate_call_id": cid,
            }
            sub_output = ""
            try:
                async for ev, final in drain_subagent_turn(
                    target,
                    from_agent_id=agent_id or "",
                    reason=reason,
                    user_request=user_request,
                    history=None,
                    session_id=session_id,
                    delegate_call_id=cid,
                    parallel_index=parallel_index,
                    parallel_total=parallel_total,
                ):
                    yield ev
                    sub_output = final
            except Exception as exc:
                sub_output = f"Error: subagent {target} failed: {exc}"
                delegate_error = True
            yield {
                "kind": "subagent_done",
                "agent_id": target,
                "content": sub_output,
                "status": "error" if delegate_error else "ok",
                "delegate_call_id": cid,
            }
            # Learning OS shared lane — publish full outcome before truncate.
            if session_id:
                try:
                    from app.models.mixins import swarm_notes as sn
                    from app.services import store

                    def _publish(conn: Any) -> None:
                        sn.insert_swarm_note(
                            conn,
                            session_id=session_id,
                            from_agent_id=agent_id or "",
                            to_agent_id=target,
                            delegate_call_id=cid or "",
                            reason=reason,
                            content=str(sub_output or ""),
                            status="error" if delegate_error else "ok",
                        )

                    store.with_db(_publish)
                except Exception:
                    _logger.debug("swarm_note publish failed", exc_info=True)
            sub_result = sub_output or "(no output from subagent)"
    elif delegate_error:
        sub_result = str(delegate_result)
    else:
        sub_result = "Error: delegate did not resolve a target agent."

    sub_result = _truncate_result(sub_result)
    if result_out is not None:
        result_out.append(sub_result)
    yield {
        "kind": "tool_result",
        "tool": call.name,
        "result": sub_result,
        "error": delegate_error,
        "call_id": cid,
    }


# Back-compat alias (tests / older callers).
async def _drain_delegate_bundle(
    *,
    cid: str,
    call: ToolCall,
    agent_id: str | None,
    user_request: str,
    parallel_index: int,
    parallel_total: int,
    session_id: str | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    """Buffered wrapper around :func:`_stream_delegate_bundle`."""
    events: list[dict[str, Any]] = []
    box: list[str] = []
    async for ev in _stream_delegate_bundle(
        cid=cid,
        call=call,
        agent_id=agent_id,
        user_request=user_request,
        parallel_index=parallel_index,
        parallel_total=parallel_total,
        session_id=session_id,
        result_out=box,
    ):
        events.append(ev)
    return cid, events, (box[0] if box else "Error: no output from subagent")


__all__ = ["run_turn"]
