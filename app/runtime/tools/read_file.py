"""read_file tool — paginated, line-numbered reads under the workplace root."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.file_util import (
    format_numbered_page,
    not_found_message,
    parse_positive_int,
)
from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc

_MAX_CHARS = 100_000
_DEFAULT_LIMIT = 500


def run(arguments: dict[str, Any]) -> str:
    """Read ``path`` with optional line pagination; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: read_file expects a dict of arguments"
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
    limit = min(int(limit), 2000)

    # Remote returns raw text; we still number/paginate locally (better UX).
    remote = try_tunnel_rpc("read_file", {"path": path_arg})
    if remote is not None:
        if remote.startswith("Error"):
            return remote
        return format_numbered_page(
            remote, offset=offset, limit=limit, max_chars=_MAX_CHARS
        )

    root = resolve_work_root()
    target = jail_path(root, path_arg)
    if isinstance(target, str):
        return target

    if not target.exists():
        return not_found_message(root, path_arg)
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

    return format_numbered_page(
        text, offset=offset, limit=limit, max_chars=_MAX_CHARS
    )


__all__ = ["run"]
