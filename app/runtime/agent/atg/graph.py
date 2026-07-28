"""ATG graph data model — pure logic, no LLM/DB access.

Implements the structures from the ATG paper: :class:`TaskNode` (an atomic
tool-use unit), :class:`TaskDAG` (dependency graph with topological wave
scheduling) and :class:`RefinementHistory` (coarse→fine compilation lineage,
used later for minimal-subgraph repair).

Tomo's tools always return a single string, so every node's declared output
is the key ``"result"``. Placeholders look like ``${node_id.result}`` and
resolve to the upstream node's result string at execution time.

Serialized form round-trips through plain JSON dicts so it can live in
session state.
"""
from __future__ import annotations

import json
import re

MAX_DEPTH = 3
MAX_NODES = 40
MAX_COMPILE_LLM_CALLS = 12
MAX_NODE_ATTEMPTS = 2
MAX_REPAIR_ATTEMPTS = 2
NODE_OUTPUT_EXCERPT_CHARS = 2000

VALID_NODE_STATUSES = {
    "pending", "ready", "running", "done", "failed", "frozen", "skipped",
}
SATISFIED_STATUSES = {"done", "frozen"}
TERMINAL_STATUSES = {"done", "frozen", "skipped"}

# "${n1.result}" — node ids may contain dots, so the *last* dot splits
# node id from output key.
PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


def parse_placeholder(ref: str):
    """Split a placeholder body 'node_id.key' into (node_id, key) or None."""
    if "." not in ref:
        return None
    node_id, key = ref.rsplit(".", 1)
    if not node_id or not key:
        return None
    return node_id, key


def find_placeholders(value) -> list:
    """Collect all '${node_id.key}' placeholder bodies in a JSON-like value."""
    refs: list[str] = []
    if isinstance(value, str):
        refs.extend(PLACEHOLDER_RE.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            refs.extend(find_placeholders(v))
    elif isinstance(value, list):
        for v in value:
            refs.extend(find_placeholders(v))
    return refs


class TaskNode:
    def __init__(
        self,
        id: str,
        goal: str,
        tool: str | None = None,
        args_template: dict | None = None,
        inputs: list | None = None,
        outputs: list | None = None,
        deps: list | None = None,
        parent_id: str | None = None,
        depth: int = 0,
        status: str = "pending",
        attempts: int = 0,
        record: dict | None = None,
    ):
        self.id = id
        self.goal = goal
        self.tool = tool  # None = composite (needs refinement, or free LLM bind)
        self.args_template: dict = args_template or {}
        self.inputs: list = inputs or []  # [{name, from_node, key}]
        self.outputs: list = outputs or []  # declared output keys (interface)
        self.deps: list = list(deps or [])
        self.parent_id = parent_id
        self.depth = depth
        self.status = status
        self.attempts = attempts
        self.record: dict = record or {}

    @property
    def is_composite(self) -> bool:
        return self.tool is None

    def record_result(
        self,
        resolved_args: dict | None = None,
        output=None,
        error: str | None = None,
        ts_start: float | None = None,
        ts_end: float | None = None,
    ) -> None:
        """Store execution state; output is excerpt-truncated for persistence."""
        excerpt = None
        if output is not None:
            text = output if isinstance(output, str) else json.dumps(
                output, ensure_ascii=False, default=str
            )
            if len(text) > NODE_OUTPUT_EXCERPT_CHARS:
                omitted = len(text) - NODE_OUTPUT_EXCERPT_CHARS
                text = text[:NODE_OUTPUT_EXCERPT_CHARS] + f"…[truncated {omitted} chars]"
            excerpt = text
        self.record = {
            "resolved_args": resolved_args,
            "output_excerpt": excerpt,
            "error": error,
            "ts_start": ts_start,
            "ts_end": ts_end,
        }

    def to_dict(self, structure_only: bool = False) -> dict:
        d = {
            "id": self.id,
            "goal": self.goal,
            "tool": self.tool,
            "args_template": self.args_template,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "deps": self.deps,
            "parent_id": self.parent_id,
            "depth": self.depth,
        }
        if not structure_only:
            d.update(
                {
                    "status": self.status,
                    "attempts": self.attempts,
                    "record": self.record,
                }
            )
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskNode":
        return cls(
            id=d["id"],
            goal=d.get("goal", ""),
            tool=d.get("tool"),
            args_template=d.get("args_template") or {},
            inputs=d.get("inputs") or [],
            outputs=d.get("outputs") or [],
            deps=d.get("deps") or [],
            parent_id=d.get("parent_id"),
            depth=d.get("depth", 0),
            status=d.get("status", "pending"),
            attempts=d.get("attempts", 0),
            record=d.get("record") or {},
        )


class TaskDAG:
    def __init__(
        self,
        root_goal: str,
        nodes: dict | None = None,
        version: int = 1,
        created_at: str | None = None,
    ):
        self.root_goal = root_goal
        self.nodes: dict = nodes or {}  # id -> TaskNode
        self.version = version
        self.created_at = created_at

    def add_node(self, node: TaskNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Duplicate node id: {node.id}")
        self.nodes[node.id] = node

    def get(self, node_id: str):
        return self.nodes.get(node_id)

    def validate(self) -> list:
        """Return a list of human-readable errors; empty list = valid."""
        errors: list[str] = []
        if len(self.nodes) > MAX_NODES:
            errors.append(f"Graph has {len(self.nodes)} nodes (max {MAX_NODES}).")

        for nid, node in self.nodes.items():
            if node.status not in VALID_NODE_STATUSES:
                errors.append(f"Node '{nid}' has invalid status '{node.status}'.")
            if node.depth > MAX_DEPTH:
                errors.append(f"Node '{nid}' exceeds max depth {MAX_DEPTH}.")
            for dep in node.deps:
                if dep == nid:
                    errors.append(f"Node '{nid}' depends on itself.")
                elif dep not in self.nodes:
                    errors.append(f"Node '{nid}' depends on unknown node '{dep}'.")
            for inp in node.inputs:
                src = inp.get("from_node")
                key = inp.get("key")
                producer = self.nodes.get(src)
                if producer is None:
                    errors.append(
                        f"Node '{nid}' input references unknown node '{src}'."
                    )
                    continue
                if src not in node.deps:
                    errors.append(
                        f"Node '{nid}' consumes output of '{src}' but does not "
                        f"declare it in deps."
                    )
                if key not in producer.outputs:
                    errors.append(
                        f"Node '{nid}' input references undeclared output "
                        f"'{src}.{key}' (declared: {producer.outputs})."
                    )
            for ref in find_placeholders(node.args_template):
                parsed = parse_placeholder(ref)
                if parsed is None:
                    errors.append(
                        f"Node '{nid}' has malformed placeholder '${{{ref}}}'."
                    )
                    continue
                src, key = parsed
                producer = self.nodes.get(src)
                if producer is None:
                    errors.append(
                        f"Node '{nid}' placeholder references unknown node '{src}'."
                    )
                    continue
                if src not in node.deps:
                    errors.append(
                        f"Node '{nid}' placeholder uses '{src}.{key}' but '{src}' "
                        f"is not in deps."
                    )
                if key not in producer.outputs:
                    errors.append(
                        f"Node '{nid}' placeholder references undeclared output "
                        f"'{src}.{key}' (declared: {producer.outputs})."
                    )

        cycle_nodes = self._cycle_nodes()
        if cycle_nodes:
            errors.append(f"Dependency cycle involving: {sorted(cycle_nodes)}.")
        return errors

    def _cycle_nodes(self) -> set:
        """Kahn elimination over the full graph; whatever remains is cyclic."""
        pending = set(self.nodes)
        while pending:
            level = {
                nid
                for nid in pending
                if all(d not in pending for d in self.nodes[nid].deps)
            }
            if not level:
                return pending
            pending -= level
        return set()

    def is_executable(self) -> bool:
        return not self.validate()

    def waves(self) -> list:
        """Topological levels of not-yet-terminal nodes.

        Nodes with a terminal status (done/frozen/skipped) are excluded and
        their dependents treated as unblocked. Returns [] partway if the
        remaining nodes contain a cycle.
        """
        pending = {
            nid
            for nid, n in self.nodes.items()
            if n.status not in TERMINAL_STATUSES
        }
        result: list[list[str]] = []
        while pending:
            level = sorted(
                nid
                for nid in pending
                if all(d not in pending for d in self.nodes[nid].deps)
            )
            if not level:
                break  # cycle among remaining nodes
            result.append(level)
            pending -= set(level)
        return result

    def to_dict(self, structure_only: bool = False) -> dict:
        return {
            "version": self.version,
            "root_goal": self.root_goal,
            "created_at": self.created_at,
            "nodes": {
                nid: n.to_dict(structure_only=structure_only)
                for nid, n in self.nodes.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskDAG":
        return cls(
            root_goal=d.get("root_goal", ""),
            nodes={
                nid: TaskNode.from_dict(nd)
                for nid, nd in (d.get("nodes") or {}).items()
            },
            version=d.get("version", 1),
            created_at=d.get("created_at"),
        )


class RefinementHistory:
    """Sequence of structural DAG snapshots, one per compilation refinement.

    Snapshots store structure only (no runtime records) to bound persistence
    size. The parent_id lineage across snapshots supports failure tracing for
    minimal-subgraph repair.
    """

    def __init__(self, entries: list | None = None):
        self.entries: list = entries or []  # [{version, refined_node_id, snapshot}]

    def record(self, refined_node_id, dag: TaskDAG) -> None:
        self.entries.append(
            {
                "version": len(self.entries) + 1,
                "refined_node_id": refined_node_id,  # None for the coarse pass
                "snapshot": dag.to_dict(structure_only=True),
            }
        )

    def parent_map(self) -> dict:
        """Merged {node_id: parent_id} across all snapshots (later entries win)."""
        merged: dict[str, str | None] = {}
        for entry in self.entries:
            for nid, nd in (entry.get("snapshot", {}).get("nodes") or {}).items():
                merged[nid] = nd.get("parent_id")
        return merged

    def ancestor_chain(self, node_id: str) -> list:
        """[node_id, parent, grandparent, ...] via parent_id lineage."""
        parents = self.parent_map()
        chain = [node_id]
        seen = {node_id}
        current = node_id
        while True:
            parent = parents.get(current)
            if parent is None or parent in seen:
                break
            chain.append(parent)
            seen.add(parent)
            current = parent
        return chain

    def to_dict(self) -> dict:
        return {"entries": self.entries}

    @classmethod
    def from_dict(cls, d: dict) -> "RefinementHistory":
        return cls(entries=(d or {}).get("entries") or [])


__all__ = [
    "MAX_DEPTH",
    "MAX_NODES",
    "MAX_COMPILE_LLM_CALLS",
    "MAX_NODE_ATTEMPTS",
    "MAX_REPAIR_ATTEMPTS",
    "PLACEHOLDER_RE",
    "parse_placeholder",
    "find_placeholders",
    "TaskNode",
    "TaskDAG",
    "RefinementHistory",
]
