"""read_file tool — paginated, line-numbered reads under the workplace root."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from app.runtime.tools.file_util import (
    format_numbered_page,
    not_found_message,
    parse_positive_int,
)
from app.runtime.tools.sandbox import current_agent_id, jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc

# Three-ceiling architecture: each bounds a different hostile-file shape.
_LINE_WINDOW = 2000  # ordinary large files
_BYTE_BUDGET = 128 * 1024  # wide-line files (minified bundles)
_DEFAULT_LIMIT = 500

# Param aliases normalized onto the canonical "path" key.
_PATH_ALIASES = (
    "file_path",
    "filePath",
    "filepath",
    "FilePath",
    "file",
    "fname",
    "filename",
    "target",
    "target_path",
    "src",
)

_BLOCKED_EXACT = {"/dev/zero", "/dev/urandom", "/dev/stdin"}
_BLOCKED_PROC_FD = re.compile(r"^/proc/\d+/fd/")

# Consumed-on-hit dedup: (agent_id, resolved_path) -> (hash, mtime, offset, limit).
# Expires on hit rather than caching indefinitely, so it can never reference
# content that has since been compacted out of context.
_dedup_cache: dict[tuple[str, str], tuple[str, float, int, int]] = {}
_DEDUP_CAP = 500


def _normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    if "path" in arguments:
        return arguments
    for alias in _PATH_ALIASES:
        if alias in arguments:
            out = dict(arguments)
            out["path"] = out.pop(alias)
            return out
    return arguments


def _is_blocked_device_path(target) -> bool:
    s = str(target)
    return s in _BLOCKED_EXACT or bool(_BLOCKED_PROC_FD.match(s))


def _spelling_candidates(path_arg: str) -> list[str]:
    """Up to 7 encoding-variant spellings of ``path_arg`` (never the original)."""
    variants = [
        path_arg.replace(" ", " "),
        path_arg.replace(" ", " "),
        unicodedata.normalize("NFC", path_arg),
        unicodedata.normalize("NFD", path_arg),
        path_arg.replace("'", "’").replace('"', "”"),
        path_arg.replace("’", "'").replace("‘", "'")
        .replace("“", '"').replace("”", '"'),
        unicodedata.normalize("NFC", path_arg.replace(" ", " ")),
    ]
    seen: list[str] = []
    for v in variants:
        if v != path_arg and v not in seen:
            seen.append(v)
        if len(seen) == 7:
            break
    return seen


def _try_spelling_candidates(root, path_arg: str):
    """Try 7 spelling-variant candidates; return (target, used_path) or None."""
    for candidate in _spelling_candidates(path_arg):
        target = jail_path(root, candidate)
        if isinstance(target, str):
            continue
        if target.exists() and target.is_file():
            return target, candidate
    return None


def _normalize_text(text: str) -> str:
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def run(arguments: dict[str, Any]) -> str:
    """Read ``path`` with optional line pagination; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: read_file expects a dict of arguments"
    arguments = _normalize_arguments(arguments)
    path_arg = arguments.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return "Error: 'path' argument must be a non-empty string"

    offset = parse_positive_int(
        arguments.get("offset", 1), 1, name="offset", minimum=1
    )
    if isinstance(offset, str):
        return offset
    limit = parse_positive_int(
        arguments.get("limit", _DEFAULT_LIMIT),
        _DEFAULT_LIMIT,
        name="limit",
        minimum=1,
    )
    if isinstance(limit, str):
        return limit
    limit = min(int(limit), _LINE_WINDOW)

    # Remote returns raw text; we still number/paginate locally (better UX).
    remote = try_tunnel_rpc("read_file", {"path": path_arg})
    if remote is not None:
        if remote.startswith("Error"):
            return remote
        return format_numbered_page(
            remote, offset=offset, limit=limit, max_chars=_BYTE_BUDGET
        )

    root = resolve_work_root()
    target = jail_path(root, path_arg)
    if isinstance(target, str):
        return target

    note = ""
    if not target.exists():
        found = _try_spelling_candidates(root, path_arg)
        if found is None:
            return not_found_message(root, path_arg)
        target, used_path = found
        note = f"Note: '{path_arg}' not found; read '{used_path}' instead (spelling auto-corrected).\n\n"

    if _is_blocked_device_path(target):
        return f"Error: refusing to read device/proc-fd path: {path_arg}"
    if not target.is_file():
        return f"Error: not a file: {path_arg}"

    try:
        data = target.read_bytes()
    except OSError as exc:
        return f"Error: could not read file: {exc}"

    if b"\x00" in data[:8192]:
        return "Error: binary files are not supported (use bash/tools for binary)"

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    text = _normalize_text(text)

    agent_id = current_agent_id() or "_default"
    try:
        mtime = target.stat().st_mtime
    except OSError:
        mtime = 0.0
    content_hash = hashlib.sha256(data).hexdigest()
    key = (agent_id, str(target))
    requested = (content_hash, mtime, offset, limit)
    if _dedup_cache.get(key) == requested:
        del _dedup_cache[key]  # consumed on hit — never references stale context
        total = len(text.splitlines())
        end = min(offset - 1 + limit, total) if total else 0
        return (
            note
            + f"(unchanged since last read: {path_arg}, lines {offset}-{end} of {total} "
            "— identical content, offset, and limit as before; re-read skipped)"
        )
    if len(_dedup_cache) >= _DEDUP_CAP:
        _dedup_cache.pop(next(iter(_dedup_cache)))
    _dedup_cache[key] = requested

    return note + format_numbered_page(
        text, offset=offset, limit=limit, max_chars=_BYTE_BUDGET
    )


__all__ = ["run"]
