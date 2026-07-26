"""read_file tool — read a text file under the agent work-dir sandbox."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc

_MAX_CHARS = 100_000


def run(arguments: dict[str, Any]) -> str:
    """Read ``path`` relative to the sandbox cwd; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: read_file expects a dict of arguments"
    path_arg = arguments.get("path")
    if not isinstance(path_arg, str):
        return "Error: 'path' argument must be a string"

    remote = try_tunnel_rpc("read_file", {"path": path_arg})
    if remote is not None:
        return remote

    root = resolve_work_root()
    target = jail_path(root, path_arg)
    if isinstance(target, str):
        return target

    if not target.exists():
        return f"Error: file not found: {path_arg}"
    if not target.is_file():
        return f"Error: not a file: {path_arg}"

    try:
        data = target.read_bytes()
    except OSError as exc:
        return f"Error: could not read file: {exc}"

    if b"\x00" in data[:8192]:
        return "Error: binary files are not supported"

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception as exc:  # pragma: no cover - defensive
            return f"Error: could not decode file: {exc}"

    if len(text) > _MAX_CHARS:
        return text[:_MAX_CHARS] + f"\n...[truncated, {len(text)} chars total]"
    return text


__all__ = ["run"]
