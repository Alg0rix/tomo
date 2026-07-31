"""LLM turn loop and tool execution.

Orchestration only — no HTTP, no SSE formatting, no persistence. The loop
calls an :class:`~app.runtime.llm.base.LLMClient` with the OpenAI tool
schemas from the registry, executes any requested tools via the registry,
and yields a stream of internal ``dict`` events for the chat layer to map
onto SSE:

* ``{"kind": "thinking", "content": str}``          # optional reasoning
* ``{"kind": "delta", "content": str}``             # streamed text token/chunk
* ``{"kind": "tool", "tool": str, "args": dict}``
* ``{"kind": "tool_result", "tool": str, "result": str, "error": bool}``
* ``{"kind": "delegate", "from": str, "to": str, "reason": str,
   "task": str, "parallel_index": int, "parallel_total": int}``
* ``{"kind": "subagent_start", "agent_id": str, "task": str,
   "parallel_index": int, "parallel_total": int}``
* ``{"kind": "subagent_done", "agent_id": str, "content": str,
   "status": "ok" | "error"}``
* ``{"kind": "final", "content": str, "already_streamed": bool}``
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

**ATG hook.** When ``enable_atg`` is set, the loop compiles a task DAG from
the user goal, executes it (streaming events), and feeds the summary back as
a user message before composing the final answer.
"""
from __future__ import annotations

import asyncio
import collections
import itertools
import json
import logging
from typing import Any, AsyncIterator

from app.runtime.agent.context import build_messages, build_system_prompt
from app.runtime.agent.subagent import (
    MAX_TOOL_RESULT_CHARS,
    depth_exceeded,
    drain_subagent_turn,
)
from app.runtime.llm import get_llm
from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.permissions.gate import apply_choice, evaluate
from app.runtime.permissions.grants import reset_outside_grant, set_outside_grant
from app.runtime.permissions import hitl as hitl_mod
from app.runtime.tools import sandbox
from app.runtime.tools.delegate import parse_delegated_id
from app.runtime.tools.registry import execute, get_openai_tools

_logger = logging.getLogger(__name__)

# Loop-detection tuning (sliding window of tool+args signatures).
_LOOP_WINDOW = 10
_LOOP_THRESHOLD = 5

# Read-only tools safe to run in parallel within one round.
_READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "search_files",
        "web_fetch",
        "web_search",
        "recall",
        "session_search",
        "list_skills",
        "list_workplaces",
        "todo",
    }
)


def _max_tool_iterations() -> int:
    from app.services import store

    try:
        raw = store.get_settings().get("max_tool_iterations", 12)
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 12


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


def _tool_result_is_error(result: Any) -> bool:
    """True when a tool string is a hard failure or non-zero bash exit."""
    text = str(result or "")
    if text.startswith("Error:") or text.startswith("BLOCKED"):
        return True
    import re

    m = re.search(r"(?m)^exit code:\s*(\d+)\s*$", text)
    if m and int(m.group(1)) != 0:
        return True
    return False


async def _run_one_gated_tool(
    call: ToolCall,
    *,
    session_id: str | None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield optional HITL events, then a ``tool_result`` event."""
    args = call.arguments if isinstance(call.arguments, dict) else {}

    if call.name == "clarify":
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
        yield {"kind": "clarify_required", **payload}
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
        return

    work_root = sandbox.resolve_work_root()
    decision = evaluate(
        call.name, args, work_root=work_root, session_id=session_id
    )
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
        yield {"kind": "approval_required", **payload}
        choice = await hitl_mod.await_approval(payload["id"])
        decision = apply_choice(decision, choice, session_id=session_id)

    if not decision.allowed:
        result = decision.message or "BLOCKED: denied"
        yield {
            "kind": "tool_result",
            "tool": call.name,
            "result": result,
            "error": True,
        }
        return

    grant_tok = set_outside_grant(decision.grant)
    try:
        result = await asyncio.to_thread(execute, call.name, args)
    except Exception as exc:
        result = f"Error: {exc}"
    finally:
        reset_outside_grant(grant_tok)

    result = _truncate_result(result)
    yield {
        "kind": "tool_result",
        "tool": call.name,
        "result": result,
        "error": _tool_result_is_error(result),
    }


def _truncate_result(result: Any) -> str:
    """Truncate oversized tool results to bound the context window."""
    raw = result if isinstance(result, str) else str(result or "")
    if len(raw) <= MAX_TOOL_RESULT_CHARS:
        return raw
    return raw[:MAX_TOOL_RESULT_CHARS] + f"\n…[truncated, {len(raw)} chars]"


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
) -> AsyncIterator[dict[str, Any]] | None:
    """Compile + execute a task DAG when ATG is enabled.

    Yields ATG events (tool/tool_result/atg_wave/atg_summary). Returns None
    (no-ATG path) when the compile fails or the caller didn't request it.
    The final ``atg_summary`` event's ``summary`` is what the loop feeds back.
    """
    goal = (user_message or "").strip()
    if not goal:
        return None
    try:
        from app.runtime.agent.atg import compile_task_graph, run_dag_execution
        from app.runtime.agent.atg.compiler import CompilationError

        dag, _history = await compile_task_graph(goal, tools, llm)
    except Exception as exc:  # CompilationError or any failure → no-ATG fallback
        _logger.warning("ATG compile failed: %s", exc)
        return None
    return run_dag_execution(dag, llm=llm, tools=tools, agent_id=agent_id)


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
    enable_atg: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Run one agent turn, yielding internal events.

    ``user_message`` is the new turn input; pass ``None`` when the caller has
    already persisted the user entry into ``history`` so it is not
    duplicated. ``llm`` / ``tools`` / ``system_prompt`` / ``max_iterations``
    default to settings-backed values but are injectable for tests.
    ``agent_id`` selects the per-agent ``SYSTEM.md`` / ``SOUL.md``;
    ``session_id`` is context for persistence wiring. ``enable_atg`` turns on
    the Atomic Task Graph path (compile + execute a DAG before the loop).

    Setup failures and per-round backend failures are surfaced as
    ``{"kind": "error", ...}`` events — ``run_turn`` never raises out to the
    consumer. The function is an async generator — iterate with ``async for``.
    """
    sandbox_token = sandbox.bind_agent(agent_id)
    try:
        try:
            client = llm if llm is not None else get_llm(agent_id)
            if tools is not None:
                tool_schemas = tools
            elif agent_id:
                from app.services import store

                tool_schemas = store.get_agent_openai_tools(agent_id)
            else:
                tool_schemas = get_openai_tools()
            limit = (
                max_iterations
                if max_iterations is not None
                else _max_tool_iterations()
            )
            prompt = system_prompt
            if prompt is None:
                prompt = build_system_prompt(agent_id)
            messages = build_messages(
                history,
                user_message,
                system_prompt=prompt,
                for_agent_id=agent_id,
            )
        except Exception as exc:
            yield {"kind": "error", "message": f"Agent setup failed: {exc}"}
            return

        _logger.info(
            "turn start agent=%s session=%s tools=%d limit=%d atg=%s",
            agent_id, session_id, len(tool_schemas), limit, enable_atg,
        )

        id_counter = itertools.count()
        user_request = _last_user_text(user_message, history)

        # ── ATG branch: front-load a compiled DAG before the tool loop ──
        if enable_atg:
            atg_gen = await _maybe_run_atg(
                user_message=user_message,
                tools=tool_schemas,
                llm=client,
                agent_id=agent_id,
            )
            if atg_gen is not None:
                atg_summary = ""
                async for ev in atg_gen:
                    ev = dict(ev)
                    ev.setdefault("agent_id", agent_id or "")
                    yield ev
                    if ev["kind"] == "atg_summary":
                        atg_summary = ev.get("summary") or ""
                if atg_summary:
                    messages.append(
                        {"role": "user", "content": f"[SYSTEM] {atg_summary}"}
                    )

        # ── Loop-detection state ──
        call_window: collections.deque[str] = collections.deque(maxlen=_LOOP_WINDOW)
        force_stopped = False

        iteration = 0
        while iteration < limit:
            iteration += 1
            resp: LLMResponse | None = None
            streamed = False
            _logger.info("LLM round %d agent=%s …", iteration, agent_id)
            try:
                async for piece in _llm_round(client, messages, tool_schemas):
                    if piece["kind"] == "delta":
                        streamed = True
                        yield piece
                    elif piece["kind"] == "_response":
                        resp = piece["response"]
            except Exception as exc:
                yield {"kind": "error", "message": f"LLM request failed: {exc}"}
                return

            if resp is None:
                yield {"kind": "error", "message": "LLM stream ended without a response"}
                return

            if resp.has_tool_calls and resp.content:
                yield {"kind": "thinking", "content": resp.content}

            if not resp.has_tool_calls:
                _logger.info("turn end agent=%s final_chars=%d", agent_id, len(resp.content or ""))
                yield {
                    "kind": "final",
                    "content": resp.content or "",
                    "already_streamed": streamed,
                }
                return

            paired = _with_ids(resp.tool_calls, id_counter)
            messages.append(_assistant_tool_calls_message(resp, paired))

            tool_names = [c.name for _cid, c in paired]
            _logger.info(
                "tool round %d: %s", iteration, tool_names,
            )

            # ── Loop detection: identical (tool, args) repeated too often ──
            for _cid, call in paired:
                sig = _call_signature(call)
                call_window.append(sig)
                if call_window.count(sig) >= _LOOP_THRESHOLD and not force_stopped:
                    force_stopped = True
                    _logger.warning(
                        "loop detected agent=%s tool=%s sig=%s — forcing stop",
                        agent_id, call.name, sig[:80],
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

            # ── Tool execution (permission-gated; serial for correct HITL) ──
            delegate_calls = [
                (cid, c) for cid, c in paired if c.name == "delegate"
            ]
            other_calls = [
                (cid, c) for cid, c in paired if c.name != "delegate"
            ]
            if delegate_calls or other_calls:
                _logger.info(
                    "exec: delegate=%d gated=%d",
                    len(delegate_calls),
                    len(other_calls),
                )

            by_cid: dict[str, tuple[ToolCall, Any]] = {}
            for cid, call in other_calls:
                yield {"kind": "tool", "tool": call.name, "args": call.arguments}
                result_text = "Error: no tool result"
                error = True
                async for ev in _run_one_gated_tool(call, session_id=session_id):
                    if ev.get("kind") in {"approval_required", "clarify_required"}:
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
                        }
                by_cid[cid] = (call, result_text)
                messages.append(
                    {"role": "tool", "tool_call_id": cid, "content": result_text}
                )

            # ── Subagent delegation: run the child, capture output, continue ──
            parallel_total = len(delegate_calls)
            for idx, (cid, call) in enumerate(delegate_calls):
                parallel_index = idx + 1
                yield {"kind": "tool", "tool": call.name, "args": call.arguments}
                args = call.arguments
                reason = args.get("reason") if isinstance(args, dict) else None
                if not isinstance(reason, str) or not reason.strip():
                    reason = "delegate"
                reason = reason.strip()

                # The delegate tool backend resolved the target id.
                delegate_result = await asyncio.to_thread(
                    execute, call.name, call.arguments
                )
                target = parse_delegated_id(str(delegate_result))
                delegate_error = _tool_result_is_error(delegate_result)

                if target and not delegate_error:
                    if depth_exceeded():
                        _logger.warning(
                            "delegate depth exceeded: agent=%s target=%s",
                            agent_id, target,
                        )
                        sub_result = (
                            f"Error: delegation depth limit reached — "
                            f"cannot delegate further to {target}."
                        )
                        delegate_error = True
                    else:
                        # Emit the handoff marker for the UI.
                        yield {
                            "kind": "delegate",
                            "from": agent_id or "",
                            "to": target,
                            "reason": reason,
                            "task": reason,
                            "parallel_index": parallel_index,
                            "parallel_total": parallel_total,
                        }
                        # Signal subagent start so the UI can create its card.
                        _logger.info(
                            "delegate→ agent=%s target=%s reason=%s parallel=%d/%d",
                            agent_id, target, reason[:60], parallel_index, parallel_total,
                        )
                        yield {
                            "kind": "subagent_start",
                            "agent_id": target,
                            "from": agent_id or "",
                            "task": reason,
                            "parallel_index": parallel_index,
                            "parallel_total": parallel_total,
                        }
                        # Run the nested subagent turn, streaming its events.
                        sub_output = ""
                        try:
                            async for ev, final in drain_subagent_turn(
                                target,
                                from_agent_id=agent_id or "",
                                reason=reason,
                                user_request=user_request,
                                history=None,
                            ):
                                yield ev
                                sub_output = final
                        except Exception as exc:
                            sub_output = f"Error: subagent {target} failed: {exc}"
                            delegate_error = True
                            _logger.error(
                                "subagent error: agent=%s target=%s err=%s",
                                agent_id, target, exc,
                            )
                        # Signal subagent completion so the UI can mark it done.
                        _logger.info(
                            "subagent done: target=%s status=%s output=%d chars",
                            target,
                            "error" if delegate_error else "ok",
                            len(sub_output),
                        )
                        yield {
                            "kind": "subagent_done",
                            "agent_id": target,
                            "content": sub_output,
                            "status": "error" if delegate_error else "ok",
                        }
                        sub_result = sub_output or "(no output from subagent)"
                elif delegate_error:
                    sub_result = str(delegate_result)
                else:
                    sub_result = "Error: delegate did not resolve a target agent."

                sub_result = _truncate_result(sub_result)
                yield {
                    "kind": "tool_result",
                    "tool": call.name,
                    "result": sub_result,
                    "error": delegate_error,
                }
                messages.append(
                    {"role": "tool", "tool_call_id": cid, "content": sub_result}
                )

            continue

        _logger.warning(
            "max iterations exceeded: agent=%s limit=%d", agent_id, limit,
        )
        yield {
            "kind": "error",
            "message": (
                f"Reached max tool iterations ({limit}) without a final answer."
            ),
        }
    finally:
        sandbox.reset_agent(sandbox_token)


__all__ = ["run_turn"]
