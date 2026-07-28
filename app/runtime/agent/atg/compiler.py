"""ATG async compiler — LLM-driven recursive graph compilation.

Coarse pass: one LLM call turns the root goal into a 3-7 node DAG.
Refinement passes: each composite node (tool=None) is expanded into a
subgraph whose external interface (declared outputs, consumed deps) equals
the composite node's — interface preservation keeps the surrounding graph
stable. Malformed or non-preserving LLM output is retried with the
validator's error text; on exhaustion the node stays atomic and the
executor free-binds it at runtime.

Every pass is recorded in a :class:`RefinementHistory` for later subgraph
repair. Adapted to Tomo's async :class:`~app.runtime.llm.base.LLMClient`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.runtime.agent.atg import prompts
from app.runtime.agent.atg.graph import (
    MAX_COMPILE_LLM_CALLS,
    MAX_DEPTH,
    MAX_NODES,
    PLACEHOLDER_RE,
    RefinementHistory,
    TaskDAG,
    TaskNode,
    find_placeholders,
    parse_placeholder,
)
from app.runtime.agent.atg.interfaces import get_interface_catalog
from app.runtime.llm.base import LLMClient

_logger = logging.getLogger(__name__)

_MAX_RETRIES_PER_PASS = 2  # coarse pass — its failure fails the whole compile
_REFINE_RETRIES = 1  # refinement — failure just leaves the node atomic
_COMPILE_MAX_TOKENS = 2048
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class CompilationError(Exception):
    pass


def _extract_json(text: str) -> dict:
    """Pull the graph JSON out of an LLM response (fenced block preferred)."""
    if not text:
        raise CompilationError("Empty LLM response.")
    obj = _extract_first_json(text)
    if obj is None:
        raise CompilationError("No JSON object found in LLM response.")
    if not isinstance(obj, dict) or not isinstance(obj.get("nodes"), list):
        raise CompilationError('JSON must be an object with a "nodes" array.')
    return obj


def _extract_first_json(text: str):
    """Best-effort extraction of the first JSON object from ``text``."""
    if not text:
        return None
    # Prefer a fenced block.
    m = _JSON_BLOCK_RE.search(text)
    if m:
        candidate = m.group(1)
    else:
        # Fall back to the first {...} span.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except (ValueError, TypeError):
        return None


class _AsyncLLMCaller:
    """Async, budget-capped LLM JSON calls."""

    def __init__(self, llm: LLMClient, log_file: str | None = None):
        self.llm = llm
        self.log_file = log_file
        self.calls = 0

    async def json_call(self, system: str, user: str) -> dict:
        if self.calls >= MAX_COMPILE_LLM_CALLS:
            raise CompilationError(
                f"Compile LLM call budget exhausted ({MAX_COMPILE_LLM_CALLS})."
            )
        self.calls += 1
        resp = await self.llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=None,
        )
        content = (resp.content or "").strip()
        if not content:
            raise CompilationError("LLM returned empty content.")
        return _extract_json(content)


def _parse_nodes(
    obj: dict, id_map: dict | None = None, parent_id: str | None = None, depth: int = 0
) -> list[TaskNode]:
    """Turn the LLM's node dicts into TaskNodes, namespacing relative ids."""
    id_map = id_map or {}

    def full_id(ref):
        return id_map.get(str(ref), str(ref))

    nodes: list[TaskNode] = []
    for nd in obj["nodes"]:
        if not isinstance(nd, dict) or not nd.get("id") or not nd.get("goal"):
            raise CompilationError(f"Every node needs 'id' and 'goal': {nd}")
        args = nd.get("args_template") or {}
        if id_map:
            args = _map_placeholder_nodes(args, id_map)
        nodes.append(
            TaskNode(
                id=full_id(nd["id"]),
                goal=str(nd["goal"]),
                tool=nd.get("tool") or None,
                args_template=args if isinstance(args, dict) else {},
                inputs=[
                    {**inp, "from_node": full_id(inp.get("from_node"))}
                    for inp in (nd.get("inputs") or [])
                    if isinstance(inp, dict)
                ],
                outputs=[str(o) for o in (nd.get("outputs") or [])],
                deps=[full_id(d) for d in (nd.get("deps") or [])],
                parent_id=parent_id,
                depth=depth,
            )
        )
    return nodes


def _map_placeholder_nodes(value, id_map: dict):
    """Rewrite '${rel.key}' placeholders through an id map (relative→full)."""
    if isinstance(value, str):
        def sub(m):
            parsed = parse_placeholder(m.group(1))
            if parsed and parsed[0] in id_map:
                return "${%s.%s}" % (id_map[parsed[0]], parsed[1])
            return m.group(0)
        return PLACEHOLDER_RE.sub(sub, value)
    if isinstance(value, dict):
        return {k: _map_placeholder_nodes(v, id_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_placeholder_nodes(v, id_map) for v in value]
    return value


def _splice(dag: TaskDAG, node_id: str, children: list[TaskNode]) -> None:
    """Replace a composite node with its refinement subgraph in place.

    Enforces interface preservation: children must collectively declare exactly
    the composite node's outputs (each key from one child), and may only
    depend on siblings or the composite node's own deps. External consumers
    are rewired to the producing child per output key.
    """
    node = dag.nodes[node_id]
    child_ids = {c.id for c in children}

    allowed_external = set(node.deps)
    for child in children:
        for dep in child.deps:
            if dep not in child_ids and dep not in allowed_external:
                raise CompilationError(
                    f"Child '{child.id}' depends on '{dep}', which is neither a "
                    f"sibling nor a dependency of '{node_id}'."
                )

    internal_deps = {d for c in children for d in c.deps if d in child_ids}
    sinks = [c.id for c in children if c.id not in internal_deps]

    producers: dict[str, str] = {}
    for key in node.outputs:
        candidates = [c.id for c in children if key in c.outputs]
        if len(candidates) > 1:
            candidates = [cid for cid in candidates if cid in sinks] or candidates
        if not candidates:
            raise CompilationError(
                f"Refinement of '{node_id}' does not produce required output "
                f"'{key}' (interface must be preserved)."
            )
        if len(candidates) > 1:
            raise CompilationError(
                f"Output '{key}' of '{node_id}' is produced ambiguously by "
                f"{candidates}; exactly one sink child must declare it."
            )
        producers[key] = candidates[0]

    for other in dag.nodes.values():
        if other.id == node_id:
            continue
        consumed_keys: set[str] = set()
        for inp in other.inputs:
            if inp.get("from_node") == node_id:
                consumed_keys.add(inp["key"])
                inp["from_node"] = producers[inp["key"]]
        for ref in find_placeholders(other.args_template):
            parsed = parse_placeholder(ref)
            if parsed and parsed[0] == node_id:
                consumed_keys.add(parsed[1])
        if node_id in other.deps:
            replacements = sorted({producers[k] for k in consumed_keys if k in producers}) or sinks
            other.deps = [d for d in other.deps if d != node_id]
            other.deps.extend(d for d in replacements if d not in other.deps)

    for other in dag.nodes.values():
        if other.id == node_id:
            continue
        other.args_template = _rewrite_consumer_placeholders(
            other.args_template, node_id, producers
        )

    del dag.nodes[node_id]
    for child in children:
        dag.add_node(child)


def _rewrite_consumer_placeholders(value, node_id: str, producers: dict):
    if isinstance(value, str):
        def sub(m):
            parsed = parse_placeholder(m.group(1))
            if parsed and parsed[0] == node_id and parsed[1] in producers:
                return "${%s.%s}" % (producers[parsed[1]], parsed[1])
            return m.group(0)
        return PLACEHOLDER_RE.sub(sub, value)
    if isinstance(value, dict):
        return {k: _rewrite_consumer_placeholders(v, node_id, producers) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_consumer_placeholders(v, node_id, producers) for v in value]
    return value


async def _coarse_pass(
    caller: _AsyncLLMCaller, root_goal: str, catalog: str, context_excerpt: str = ""
) -> TaskDAG:
    system = prompts.COMPILE_SYSTEM.format(catalog=catalog, schema=prompts.GRAPH_JSON_SCHEMA)
    context = f"\nContext:\n{context_excerpt}" if context_excerpt else ""
    user = prompts.COMPILE_COARSE_USER.format(goal=root_goal, context=context)

    last_error: str | None = None
    for _attempt in range(1 + _MAX_RETRIES_PER_PASS):
        prompt = user if last_error is None else (
            user + prompts.COMPILE_RETRY_SUFFIX.format(errors=last_error))
        try:
            obj = await caller.json_call(system, prompt)
            dag = TaskDAG(root_goal=root_goal)
            for node in _parse_nodes(obj):
                dag.add_node(node)
            errors = dag.validate()
            if errors:
                raise CompilationError("; ".join(errors))
            if not dag.nodes:
                raise CompilationError("Graph has no nodes.")
            return dag
        except CompilationError as e:
            last_error = str(e)
            _logger.warning("ATG coarse pass attempt failed: %s", e)
    raise CompilationError(f"Coarse compilation failed: {last_error}")


async def _refine_node(
    caller: _AsyncLLMCaller, dag: TaskDAG, node_id: str, catalog: str
) -> TaskDAG:
    """One refinement pass. Returns the new DAG (raises on failure)."""
    node = dag.nodes[node_id]
    external: list[str] = []
    for dep in node.deps:
        producer = dag.nodes.get(dep)
        if producer:
            external.append(f"{dep} (outputs: {producer.outputs})")
    system = prompts.COMPILE_SYSTEM.format(catalog=catalog, schema=prompts.GRAPH_JSON_SCHEMA)
    user = prompts.COMPILE_REFINE_USER.format(
        node_id=node_id,
        goal=node.goal,
        external_context=", ".join(external) or "(none)",
        outputs=node.outputs,
        schema=prompts.GRAPH_JSON_SCHEMA,
    )

    last_error: str | None = None
    for _attempt in range(1 + _REFINE_RETRIES):
        prompt = user if last_error is None else (
            user + prompts.COMPILE_RETRY_SUFFIX.format(errors=last_error))
        try:
            obj = await caller.json_call(system, prompt)
            work = TaskDAG.from_dict(dag.to_dict())
            work.version = dag.version
            id_map = {
                str(nd.get("id")): f"{node_id}.{nd.get('id')}"
                for nd in obj["nodes"]
                if isinstance(nd, dict) and nd.get("id")
            }
            children = _parse_nodes(obj, id_map=id_map, parent_id=node_id, depth=node.depth + 1)
            if len(work.nodes) - 1 + len(children) > MAX_NODES:
                raise CompilationError(f"Refinement would exceed {MAX_NODES} nodes.")
            _splice(work, node_id, children)
            errors = work.validate()
            if errors:
                raise CompilationError("; ".join(errors))
            return work
        except CompilationError as e:
            last_error = str(e)
            _logger.warning("ATG refinement of %s attempt failed: %s", node_id, e)
    raise CompilationError(f"Refinement of '{node_id}' failed: {last_error}")


async def compile_task_graph(
    root_goal: str,
    tools: list[dict[str, Any]],
    llm: LLMClient,
    log_file: str | None = None,
    context_excerpt: str = "",
) -> tuple[TaskDAG, RefinementHistory]:
    """Full compilation: coarse pass + BFS refinement of composite nodes.

    Returns ``(TaskDAG, RefinementHistory)``. A composite node whose
    refinement fails stays atomic (tool=None) — the executor free-binds it at
    runtime. Raises :class:`CompilationError` only when the coarse pass fails.
    """
    caller = _AsyncLLMCaller(llm, log_file=log_file)
    catalog = get_interface_catalog(tools)
    history = RefinementHistory()

    dag = await _coarse_pass(caller, root_goal, catalog, context_excerpt)
    history.record(None, dag)

    queue = [nid for nid, n in sorted(dag.nodes.items()) if n.is_composite]
    while queue:
        node_id = queue.pop(0)
        node = dag.nodes.get(node_id)
        if node is None or not node.is_composite:
            continue
        if node.depth >= MAX_DEPTH:
            continue  # stays atomic; executor free-binds it
        if caller.calls >= MAX_COMPILE_LLM_CALLS:
            _logger.warning(
                "ATG compile budget exhausted; remaining composite nodes stay atomic: %s",
                [node_id] + queue,
            )
            break
        try:
            dag = await _refine_node(caller, dag, node_id, catalog)
        except CompilationError as e:
            _logger.warning(
                "ATG: node %s stays atomic after failed refinement (%s) — "
                "skipping refinement for remaining composites: %s",
                node_id, e, queue,
            )
            break
        dag.version += 1
        history.record(node_id, dag)
        queue.extend(
            nid
            for nid, n in sorted(dag.nodes.items())
            if n.is_composite and n.parent_id == node_id
        )

    return dag, history


def render_markdown(dag: TaskDAG, history: RefinementHistory | None = None) -> str:
    """Human-readable plan-file rendering of the compiled graph."""
    lines = [
        "# Task Graph (ATG)",
        "",
        f"**Goal**: {dag.root_goal}",
        f"**Nodes**: {len(dag.nodes)} — **version**: {dag.version}",
        "",
        "## Execution waves",
        "",
    ]
    for i, wave in enumerate(dag.waves(), 1):
        parallel = " (parallel)" if len(wave) > 1 else ""
        lines.append(f"### Wave {i}{parallel}")
        for nid in wave:
            node = dag.nodes[nid]
            tool = f"`{node.tool}`" if node.tool else "_composite (LLM-bound)_"
            deps = f" ← {', '.join(node.deps)}" if node.deps else ""
            lines.append(f"- **{nid}** {tool}: {node.goal}{deps}")
        lines.append("")
    if history and len(history.entries) > 1:
        refined = [e["refined_node_id"] for e in history.entries[1:]]
        lines.append(
            f"_Refinement history: coarse graph + {len(refined)} refinement(s) ({', '.join(refined)})._"
        )
    return "\n".join(lines)


__all__ = ["CompilationError", "compile_task_graph", "render_markdown"]
