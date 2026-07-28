"""ATG async executor — dependency-aware scheduling of a compiled TaskDAG.

Executes the graph wave by wave (topological levels): read-only nodes in a
wave run in parallel via :func:`asyncio.gather`, mutating nodes run serially
in node-id order. Node arguments are bound either directly from the compiled
``args_template`` (placeholders resolved from upstream outputs) or, when that
is not possible, by a localized async LLM call that sees ONLY the node's
goal, tool schema and declared upstream excerpts — never the conversation
history.

The executor is an **async generator** that yields internal events (the same
``kind`` vocabulary the agent loop uses, tagged with ``atg_node``) so the
loop can stream them to SSE. A final ``atg_summary`` event carries the
result summary the parent loop feeds back to the model.

Failure policy: a node that fails after binding attempts marks the run as
``fallback`` — remaining nodes are skipped and the partial results are
summarised so the plain loop can continue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

from app.runtime.agent.atg import prompts
from app.runtime.agent.atg.compiler import _extract_first_json
from app.runtime.agent.atg.graph import (
    PLACEHOLDER_RE,
    RefinementHistory,
    TaskDAG,
    TaskNode,
    parse_placeholder,
)
from app.runtime.agent.atg.interfaces import get_interface_catalog, is_read_only
from app.runtime.llm.base import LLMClient

_logger = logging.getLogger(__name__)

_BIND_ATTEMPTS = 2
_SUMMARY_EXCERPT_CHARS = 300


class AtgOutcome:
    """Result of a DAG execution run."""

    def __init__(
        self,
        status: str,
        summary_for_llm: str = "",
        stopped: bool = False,
        stats: dict | None = None,
    ):
        self.status = status  # done | fallback | failed
        self.summary_for_llm = summary_for_llm
        self.stopped = stopped
        self.stats = stats or {}


class _NodeError(Exception):
    """A node-level failure (binding, rejection, tool error)."""


def _is_error_result(result: Any) -> bool:
    """Tomo tools return strings; ``Error:`` prefix or empty = failure."""
    text = str(result or "")
    if text.startswith("Error:"):
        return True
    return not text.strip()


def _lookup_output(outputs: dict, node_id: str, key: str):
    value = outputs.get(node_id)
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and key in value:
        return value[key]
    return None


def _resolve_template(args_template: dict, outputs: dict):
    """Resolve ``${node.key}`` placeholders from upstream outputs.

    Returns ``(resolved_args, fully_resolved)``. String values that are
    exactly one placeholder keep the raw upstream value; embedded
    placeholders are substituted as strings.
    """
    unresolved: list[str] = []

    def resolve(value):
        if isinstance(value, str):
            m = PLACEHOLDER_RE.fullmatch(value.strip())
            if m:
                parsed = parse_placeholder(m.group(1))
                if parsed:
                    v = _lookup_output(outputs, *parsed)
                    if v is None:
                        unresolved.append(m.group(1))
                        return value
                    return v

            def sub(match):
                parsed_ = parse_placeholder(match.group(1))
                v = _lookup_output(outputs, *parsed_) if parsed_ else None
                if v is None:
                    unresolved.append(match.group(1))
                    return match.group(0)
                return v if isinstance(v, str) else json.dumps(v, default=str)

            return PLACEHOLDER_RE.sub(sub, value)
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        return value

    resolved = resolve(args_template)
    return resolved, not unresolved


def _upstream_digest(node: TaskNode, dag: TaskDAG, outputs: dict) -> str:
    """Localized context: excerpts of the node's declared upstream outputs."""
    lines: list[str] = []
    for dep in node.deps:
        producer = dag.get(dep)
        value = outputs.get(dep)
        if value is None and producer is not None:
            value = producer.record.get("output_excerpt")
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if text and len(text) > 1500:
            text = text[:1500] + "…[truncated]"
        lines.append(f"- {dep} ({producer.goal if producer else '?'}): {text or '(no output)'}")
    return "\n".join(lines) or "(none)"


async def _bind_node_via_llm(
    node: TaskNode,
    dag: TaskDAG,
    outputs: dict,
    llm: LLMClient,
    available_tools: list[dict[str, Any]],
    feedback: str | None = None,
) -> tuple[str, dict]:
    """One localized async LLM call to produce (tool, args) for this node."""
    catalog = get_interface_catalog(available_tools)
    tool_names = {(t.get("function") or {}).get("name") for t in available_tools}
    tool_constraint = (
        f"You MUST use the tool: {node.tool}" if node.tool
        else "Choose the most suitable tool from the catalog."
    )
    user = prompts.NODE_BIND_USER.format(
        goal=node.goal,
        tool_constraint=tool_constraint,
        args_template=json.dumps(node.args_template, default=str),
        upstream=_upstream_digest(node, dag, outputs),
    )
    if feedback:
        user += f"\n\nReviewer feedback on the previous attempt: {feedback}"
    system = prompts.NODE_BIND_SYSTEM.format(catalog=catalog)

    last_error: str | None = None
    for _ in range(_BIND_ATTEMPTS):
        prompt = user if last_error is None else (
            user + prompts.NODE_BIND_RETRY_SUFFIX.format(errors=last_error))
        resp = await llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            tools=None,
        )
        content = (resp.content or "").strip()
        obj = _extract_first_json(content)
        if obj is None:
            last_error = "invalid JSON: no parseable object in response"
            continue
        tool = node.tool or obj.get("tool")
        args = obj.get("args")
        if not tool or tool not in tool_names:
            last_error = f"unknown tool '{tool}'"
            continue
        if not isinstance(args, dict):
            last_error = "'args' must be an object"
            continue
        return tool, args
    raise _NodeError(f"argument binding failed: {last_error}")


async def _bind_node(
    node: TaskNode,
    dag: TaskDAG,
    outputs: dict,
    llm: LLMClient,
    tools: list[dict[str, Any]],
) -> tuple[str, dict]:
    """Resolve (tool, args) — direct template resolution when possible,
    localized async LLM bind otherwise. Raises :class:`_NodeError`."""
    resolved, fully = _resolve_template(node.args_template, outputs)
    if node.tool and fully and node.args_template:
        return node.tool, resolved
    return await _bind_node_via_llm(node, dag, outputs, llm, tools)


async def _execute_one(
    node: TaskNode,
    dag: TaskDAG,
    outputs: dict,
    llm: LLMClient,
    tools: list[dict[str, Any]],
) -> dict:
    """Bind and execute one node in-place; returns an event dict.

    The dict has ``tool``, ``args``, ``result``, ``error``, ``atg_node`` — ready
    to be turned into ``tool``/``tool_result`` events by the caller.
    """
    from app.runtime.tools.registry import execute as _execute_tool

    ts_start = time.time()
    node.status = "running"
    node.attempts += 1
    try:
        tool, args = await _bind_node(node, dag, outputs, llm, tools)
    except _NodeError as e:
        node.status = "failed"
        node.record_result(error=str(e), ts_start=ts_start, ts_end=time.time())
        return {"tool": node.tool or "?", "args": {}, "result": str(e),
                "error": True, "atg_node": node.id}
    result = await asyncio.to_thread(_execute_tool, tool, args)
    has_error = _is_error_result(result)
    ts_end = time.time()
    node.tool = tool
    node.status = "failed" if has_error else "done"
    node.record_result(
        resolved_args=args,
        output=None if has_error else result,
        error=str(result) if has_error else None,
        ts_start=ts_start,
        ts_end=ts_end,
    )
    if not has_error:
        outputs[node.id] = result
    return {"tool": tool, "args": args, "result": result, "error": has_error,
            "atg_node": node.id}


def _seed_outputs(dag: TaskDAG) -> dict:
    """Rebuild in-memory outputs from persisted records (resume after restart)."""
    outputs: dict[str, Any] = {}
    for nid, node in dag.nodes.items():
        if node.status in ("done", "frozen") and node.record.get("output_excerpt"):
            outputs[nid] = node.record["output_excerpt"]
    return outputs


def _mark_skipped(dag: TaskDAG) -> None:
    for node in dag.nodes.values():
        if node.status in ("pending", "ready", "running"):
            node.status = "skipped"


def _summarize(dag: TaskDAG, status: str, failed_node: TaskNode | None = None) -> str:
    lines = [f"[ATG] Task graph execution — status: {status}. Goal: {dag.root_goal}"]
    done = [n for n in dag.nodes.values() if n.status == "done"]
    lines.append(f"{len(done)}/{len(dag.nodes)} nodes completed.")
    lines.append("Results:")
    for nid in sorted(dag.nodes):
        node = dag.nodes[nid]
        if node.status == "done":
            excerpt = (node.record.get("output_excerpt") or "")[:_SUMMARY_EXCERPT_CHARS]
            lines.append(
                f"- {nid} {node.tool}({json.dumps(node.record.get('resolved_args') or {}, default=str)[:120]}): ok — {excerpt}"
            )
        elif node.status == "failed":
            lines.append(f"- {nid} ({node.goal}): FAILED — {node.record.get('error')}")
        elif node.status == "skipped":
            lines.append(f"- {nid} ({node.goal}): skipped")
    if status == "done":
        lines.append(
            "All graph nodes completed. Use these results to finish the task "
            "and compose the final answer for the user."
        )
    else:
        lines.append(
            "Graph execution could not complete. The completed node outputs "
            "above are valid — continue the task manually with normal tool "
            "calls from this point."
        )
    return "\n".join(lines)


async def run_dag_execution(
    dag: TaskDAG,
    *,
    llm: LLMClient,
    tools: list[dict[str, Any]],
    agent_id: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Execute ``dag`` wave by wave, yielding internal events.

    Yields ``tool``/``tool_result`` events (tagged with ``atg_node`` and
    ``agent_id``) plus ``atg_wave`` markers and a final ``atg_summary`` event.
    The caller drains the generator and captures the summary from the final
    ``atg_summary`` event.
    """
    outputs = _seed_outputs(dag)
    stats: dict[str, Any] = {"waves_executed": 0, "parallel_peak": 0}
    waves = dag.waves()

    yield {"kind": "atg_wave", "phase": "start", "nodes_total": len(dag.nodes),
           "agent_id": agent_id or ""}

    while waves:
        if stop_event is not None and stop_event.is_set():
            _mark_skipped(dag)
            yield {"kind": "atg_summary", "summary": _summarize(dag, "failed"),
                   "status": "failed", "stopped": True, "agent_id": agent_id or ""}
            return

        wave_ids = waves[0]
        wave_nodes = [dag.nodes[nid] for nid in wave_ids]

        failed_here = [n for n in wave_nodes if n.status == "failed"]
        if failed_here:
            failed_node = failed_here[0]
            _mark_skipped(dag)
            yield {"kind": "atg_wave", "phase": "end", "status": "fallback",
                   "agent_id": agent_id or ""}
            yield {"kind": "atg_summary",
                   "summary": _summarize(dag, "fallback", failed_node),
                   "status": "fallback", "agent_id": agent_id or ""}
            return

        parallel_nodes = [
            n for n in wave_nodes
            if n.tool and is_read_only(n.tool) and n.args_template
        ]
        serial_nodes = [n for n in wave_nodes if n not in parallel_nodes]
        stats["waves_executed"] += 1
        yield {"kind": "atg_wave", "phase": "execute", "wave": wave_ids,
               "parallel": [n.id for n in parallel_nodes], "agent_id": agent_id or ""}

        # Parallel read-only nodes run concurrently; events emitted in id order.
        if parallel_nodes and len(parallel_nodes) > 1:
            stats["parallel_peak"] = max(stats["parallel_peak"], len(parallel_nodes))
            results = await asyncio.gather(
                *(_execute_one(n, dag, outputs, llm, tools) for n in parallel_nodes),
                return_exceptions=True,
            )
            by_node = {r["atg_node"]: r for r in results if isinstance(r, dict)}
            for n in parallel_nodes:
                ev = by_node.get(n.id)
                if ev:
                    yield _tool_event(ev, agent_id)
                    yield _tool_result_event(ev, agent_id)
        else:
            serial_nodes = parallel_nodes + serial_nodes

        for node in serial_nodes:
            if stop_event is not None and stop_event.is_set():
                _mark_skipped(dag)
                yield {"kind": "atg_summary", "summary": _summarize(dag, "failed"),
                       "status": "failed", "stopped": True, "agent_id": agent_id or ""}
                return
            ev = await _execute_one(node, dag, outputs, llm, tools)
            yield _tool_event(ev, agent_id)
            yield _tool_result_event(ev, agent_id)
            if ev["error"]:
                _mark_skipped(dag)
                yield {"kind": "atg_wave", "phase": "end", "status": "fallback",
                       "agent_id": agent_id or ""}
                yield {"kind": "atg_summary",
                       "summary": _summarize(dag, "fallback", node),
                       "status": "fallback", "agent_id": agent_id or ""}
                return

        waves = dag.waves()

    failed = [n for n in dag.nodes.values() if n.status == "failed"]
    status = "fallback" if failed else "done"
    yield {"kind": "atg_wave", "phase": "end", "status": status,
           "agent_id": agent_id or ""}
    yield {"kind": "atg_summary",
           "summary": _summarize(dag, status, failed[0] if failed else None),
           "status": status, "stats": stats, "agent_id": agent_id or ""}


def _tool_event(ev: dict, agent_id: str | None) -> dict:
    return {"kind": "tool", "tool": ev["tool"], "args": ev["args"],
            "atg_node": ev["atg_node"], "agent_id": agent_id or ""}


def _tool_result_event(ev: dict, agent_id: str | None) -> dict:
    return {"kind": "tool_result", "tool": ev["tool"], "result": ev["result"],
            "error": ev["error"], "atg_node": ev["atg_node"],
            "agent_id": agent_id or ""}


__all__ = ["AtgOutcome", "run_dag_execution"]
