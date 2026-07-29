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

    creating = is_create_new_file_patch(patch_text)
    if not target.exists():
        if not creating:
            return f"Error: file not found: {path_arg}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = ""
        except OSError as exc:
            return f"Error: could not create file: {exc}"
    else:
        if not target.is_file():
            return f"Error: not a file: {path_arg}"
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            return f"Error: could not read file: {exc}"
        except UnicodeDecodeError:
            return "Error: file is not valid UTF-8"

    result = apply_patch_to_content(raw, patch_text)
    if "error" in result:
        return f"Error: {result['error']}"

    content = str(result["content"])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"Error: could not write file: {exc}"

    n = int(result.get("hunks_applied") or 0)
    return f"Applied {n} hunk(s) to {path_arg}"


__all__ = ["run"]
