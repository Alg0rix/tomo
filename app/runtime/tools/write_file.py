"""write_file tool — create/overwrite a text file under the sandbox cwd."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc


def run(arguments: dict[str, Any]) -> str:
    """Write ``content`` to ``path`` under the sandbox; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: write_file expects a dict of arguments"
    path_arg = arguments.get("path")
    if not isinstance(path_arg, str):
        return "Error: 'path' argument must be a string"
    content = arguments.get("content")
    if not isinstance(content, str):
        return "Error: 'content' argument must be a string"

    remote = try_tunnel_rpc(
        "write_file",
        {"path": path_arg, "content": content, "mode": "overwrite"},
    )
    if remote is not None:
        return remote

    root = resolve_work_root()
    target = jail_path(root, path_arg)
    if isinstance(target, str):
        return target

    try:
        # Parents are under ``root`` because ``target`` itself is jailed.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error: could not write file: {exc}"

    return f"Wrote {len(content)} bytes to {path_arg}"


__all__ = ["run"]
