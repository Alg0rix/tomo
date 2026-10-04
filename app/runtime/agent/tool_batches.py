"""Ordered parallel runs: explicit safe tools, disjoint local paths, serial barriers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from app.runtime.llm.base import ToolCall
from app.runtime.tools import sandbox

_PARALLEL_SAFE = frozenset({
    "web_fetch", "web_search", "session_search", "recall_episodes", "agent_info",
    "list_skills", "list_workplaces", "use_skill",
})
_READERS = frozenset({"read_file", "search_files", "list_dir"})
_WRITERS = frozenset({"write_file", "patch", "str_replace", "delete_file"})


def _canonical_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = sandbox.resolve_work_root() / path
    return Path(os.path.normcase(os.path.realpath(path)))


def _scope(call: ToolCall) -> tuple[list[Path], bool] | None:
    args = call.arguments
    if not isinstance(args, dict):
        return None
    if call.name == "list_artifacts":
        from app.runtime.artifacts.fs import artifacts_dir, current_session_id, safe_session_id

        sid = safe_session_id(str(args.get("session_id") or current_session_id() or "").strip())
        if not sid:
            return None
        return [_canonical_path(str(artifacts_dir(sid)))], False
    if call.name == "vision_analyze":
        source = str(args.get("source") or args.get("image_url") or "").strip()
        if not source:
            return None
        if source.startswith(("http://", "https://", "data:")):
            return [], False
        if source.startswith("attachment:"):
            from app.services import store

            attachment = store.get_attachment(source[len("attachment:"):].strip())
            source = str(attachment.get("file_path") or "") if attachment else ""
            if not source:
                return None
            # Attachment paths are read relative to process cwd, not the workplace.
            return [_canonical_path(str(Path(source).absolute()))], False
        # Vision reads local bytes even when a remote workplace is selected.
        return [_canonical_path(source)], False
    if call.name in _READERS | _WRITERS:
        from app.runtime.tools.workplace_remote import resolve_agent_workplace

        workplace = resolve_agent_workplace()
        # Remote symlinks/case rules cannot be resolved on the control host.
        if workplace and workplace.get("kind") != "local":
            return None
        if call.name == "read_file":
            from app.runtime.tools.read_file import _normalize_arguments

            args = _normalize_arguments(args)
        raw = args.get("path")
        if call.name in {"search_files", "list_dir"}:
            raw = raw or "."
        if not isinstance(raw, str) or not raw.strip():
            return None
        path = _canonical_path(raw)
        # read_file may auto-correct a missing path's spelling. Its actual
        # target is then unknown until dispatch, so it must be a barrier.
        if call.name == "read_file" and not path.exists():
            return None
        return [path], call.name in _WRITERS
    if call.name in _PARALLEL_SAFE:
        return [], False
    if call.name.startswith("mcp__"):
        from app.services import store

        # Use the persisted item: sanitized runtime IDs are not server identities.
        item = store.get_mcp_item_by_runtime_id(call.name)
        server = store.get_mcp_server(item["server_id"]) if item else None
        if item and item.get("enabled") and server and server.get("enabled") and server.get("supports_parallel_tool_calls"):
            return [], False
    return None


def _overlap(a: Path, b: Path) -> bool:
    n = min(len(a.parts), len(b.parts))
    return a.parts[:n] == b.parts[:n]


def plan_tool_segments(paired: list[tuple[str, ToolCall]]) -> Iterator[list[tuple[str, ToolCall]]]:
    """Yield in call order; resolve later scopes after earlier barriers execute.

    Consecutive delegates retain the existing parallel-subagent behavior, but
    no ordinary tool may cross a delegation boundary.
    """
    current: list[tuple[str, ToolCall]] = []
    reservations: list[tuple[Path, bool]] = []
    for cid, call in paired:
        delegate = call.name == "delegate"
        if current and (current[0][1].name == "delegate") != delegate:
            yield current
            current, reservations = [], []
        if delegate:
            current.append((cid, call))
            continue
        scope = _scope(call)
        if scope is None:
            if current:
                yield current
                current, reservations = [], []
            yield [(cid, call)]
            continue
        paths, writer = scope
        if any((writer or other_writer) and _overlap(path, other)
               for path in paths for other, other_writer in reservations):
            yield current
            current, reservations = [], []
        current.append((cid, call))
        reservations.extend((path, writer) for path in paths)
    if current:
        yield current
