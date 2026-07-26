"""str_replace tool — replace a unique substring in a sandbox file."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc


def run(arguments: dict[str, Any]) -> str:
    """Replace ``old_string`` with ``new_string`` in ``path``; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: str_replace expects a dict of arguments"
    path_arg = arguments.get("path")
    if not isinstance(path_arg, str):
        return "Error: 'path' argument must be a string"
    old = arguments.get("old_string")
    if not isinstance(old, str) or old == "":
        return "Error: 'old_string' argument must be a non-empty string"
    new = arguments.get("new_string")
    if not isinstance(new, str):
        return "Error: 'new_string' argument must be a string"

    remote = try_tunnel_rpc(
        "str_replace",
        {"path": path_arg, "old_string": old, "new_string": new},
    )
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
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Error: could not read file: {exc}"
    except UnicodeDecodeError:
        return "Error: file is not valid UTF-8"

    count = text.count(old)
    if count == 0:
        return "Error: old_string not found in file"
    if count > 1:
        return f"Error: old_string matched {count} times; must be unique"

    updated = text.replace(old, new, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return f"Error: could not write file: {exc}"

    return f"Replaced 1 occurrence in {path_arg}"


__all__ = ["run"]
