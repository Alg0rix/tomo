"""patch tool — apply a unified diff to a sandbox file."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.text_edit import apply_patch_to_content, is_create_new_file_patch
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc


def run(arguments: dict[str, Any]) -> str:
    """Apply unified-diff ``patch`` to ``path``; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: patch expects a dict of arguments"
    from app.runtime.tools.sandbox import dispatch_execution

    dispatched = dispatch_execution("patch", arguments)
    if dispatched is not None:
        return dispatched
    from app.runtime.tools.sandbox import file_execution_guard

    try:
        with file_execution_guard():
            return _run(arguments)
    except PermissionError as exc:
        return f"Error: {exc}"


def _run(arguments: dict[str, Any]) -> str:
    path_arg = arguments.get("path")
    if not isinstance(path_arg, str):
        return "Error: 'path' argument must be a string"
    patch_text = arguments.get("patch")
    if not isinstance(patch_text, str) or not patch_text.strip():
        return "Error: 'patch' argument must be a non-empty string"

    remote = try_tunnel_rpc(
        "patch",
        {"path": path_arg, "patch": patch_text},
    )
    if remote is not None:
        return remote

    root = resolve_work_root()
    target = jail_path(root, path_arg)
    if isinstance(target, str):
        return target

    from app.runtime.isolation.file_edit import locked_edit

    creating = not target.exists()
    if creating and not is_create_new_file_patch(patch_text):
        return f"Error: file not found: {path_arg}"
    if not creating and not target.is_file():
        return f"Error: not a file: {path_arg}"
    initial = apply_patch_to_content("", patch_text) if creating else None
    if initial and "error" in initial:
        return f"Error: {initial['error']}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with locked_edit(target, create=creating) as edit:
            result = initial if initial is not None else apply_patch_to_content(edit.text, patch_text)
            if "error" in result:
                return f"Error: {result['error']}"
            edit.write(str(result["content"]))
    except OSError as exc:
        return f"Error: could not edit file: {exc}"
    except UnicodeDecodeError:
        return "Error: file is not valid UTF-8"

    n = int(result.get("hunks_applied") or 0)
    return f"Applied {n} hunk(s) to {path_arg}"


__all__ = ["run"]
