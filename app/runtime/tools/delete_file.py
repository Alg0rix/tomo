"""delete_file tool — remove a file under the sandbox cwd (not directories)."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc


def run(arguments: dict[str, Any]) -> str:
    """Delete ``path`` if it is a file under the sandbox; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: delete_file expects a dict of arguments"
    path_arg = arguments.get("path")
    if not isinstance(path_arg, str):
        return "Error: 'path' argument must be a string"

    remote = try_tunnel_rpc("delete_file", {"path": path_arg})
    if remote is not None:
        return remote

    root = resolve_work_root()
    target = jail_path(root, path_arg)
    if isinstance(target, str):
        return target

    if not target.exists():
        return f"Error: file not found: {path_arg}"
    if target.is_dir():
        return "Error: path is a directory; delete_file only removes files"
    if not target.is_file():
        return f"Error: not a file: {path_arg}"

    try:
        target.unlink()
    except OSError as exc:
        return f"Error: could not delete file: {exc}"

    return f"Deleted {path_arg}"


__all__ = ["run"]
