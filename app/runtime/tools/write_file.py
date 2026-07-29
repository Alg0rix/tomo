"""write_file tool — create / overwrite / append under the workplace root."""

from __future__ import annotations

from typing import Any

from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc

_MODES = frozenset({"create", "overwrite", "append"})


def run(arguments: dict[str, Any]) -> str:
    """Write ``content`` to ``path``; always returns a string.

    Modes (better than Evonic hard-refuse / Hermes always-overwrite):
    - ``overwrite`` (default) — create or replace entire file
    - ``create`` — fail if file already exists (safe new files)
    - ``append`` — append to existing or create
    """
    if not isinstance(arguments, dict):
        return "Error: write_file expects a dict of arguments"
    path_arg = arguments.get("path")
    if not isinstance(path_arg, str) or not path_arg.strip():
        return "Error: 'path' argument must be a non-empty string"
    content = arguments.get("content")
    if not isinstance(content, str):
        return "Error: 'content' argument must be a string"

    mode = str(arguments.get("mode") or "overwrite").strip().lower()
    # Evonic-style: overwrite=false maps to create
    if "overwrite" in arguments and arguments.get("overwrite") is False:
        mode = "create"
    if mode not in _MODES:
        return "Error: 'mode' must be create, overwrite, or append"

    remote = try_tunnel_rpc(
        "write_file",
        {"path": path_arg, "content": content, "mode": mode},
    )
    if remote is not None:
        return remote

    root = resolve_work_root()
    target = jail_path(root, path_arg)
    if isinstance(target, str):
        return target

    exists = target.exists()
    if exists and not target.is_file():
        return f"Error: not a file: {path_arg}"
    if mode == "create" and exists:
        return (
            f"Error: file already exists: {path_arg}. "
            "Use mode='overwrite' to replace, or str_replace/patch for edits."
        )
    if mode == "append" and exists is False:
        # create-on-append is fine
        pass

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with target.open("a", encoding="utf-8") as f:
                f.write(content)
            verb = "Appended"
        else:
            target.write_text(content, encoding="utf-8")
            verb = "Created" if not exists else "Wrote"
    except OSError as exc:
        return f"Error: could not write file: {exc}"

    return f"{verb} {len(content)} bytes to {path_arg}"


__all__ = ["run"]
