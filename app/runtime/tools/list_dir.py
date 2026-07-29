"""list_dir tool — walk folders under the workplace / sandbox root."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.runtime.tools.sandbox import jail_path, resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc

_MAX_ENTRIES = 200
_SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tomo"}


def run(arguments: dict[str, Any]) -> str:
    """List directory entries under the sandbox/workplace root."""
    if not isinstance(arguments, dict):
        return "Error: list_dir expects a dict of arguments"
    path_arg = arguments.get("path", ".")
    if not isinstance(path_arg, str) or not path_arg.strip():
        path_arg = "."
    recursive = bool(arguments.get("recursive", False))
    max_depth = arguments.get("max_depth", 2)
    try:
        max_depth = int(max_depth)
    except (TypeError, ValueError):
        max_depth = 2
    max_depth = max(0, min(max_depth, 6))

    remote = try_tunnel_rpc(
        "list_dir",
        {
            "path": path_arg,
            "recursive": recursive,
            "max_depth": max_depth,
        },
    )
    if remote is not None:
        return remote

    root = resolve_work_root()
    target = jail_path(root, path_arg)
    if isinstance(target, str):
        return target

    if not target.exists():
        return f"Error: path not found: {path_arg}"
    if not target.is_dir():
        return f"Error: not a directory: {path_arg}"

    lines: list[str] = []
    capped = False

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(root)).replace("\\", "/") or "."
        except ValueError:
            return str(p)

    def walk(dir_path: Path, depth: int) -> None:
        nonlocal capped
        if capped:
            return
        try:
            kids = sorted(
                dir_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError as exc:
            lines.append(f"! {rel(dir_path)}: {exc}")
            return
        for child in kids:
            if capped:
                return
            if child.name in _SKIP:
                continue
            r = rel(child)
            if child.is_dir():
                lines.append(f"dir  {r}/")
                if len(lines) >= _MAX_ENTRIES:
                    capped = True
                    return
                if recursive and depth < max_depth:
                    walk(child, depth + 1)
            elif child.is_file():
                try:
                    size = child.stat().st_size
                except OSError:
                    size = -1
                lines.append(f"file {r}  ({size} B)" if size >= 0 else f"file {r}")
                if len(lines) >= _MAX_ENTRIES:
                    capped = True
                    return
            else:
                lines.append(f"other {r}")
                if len(lines) >= _MAX_ENTRIES:
                    capped = True
                    return

    root_label = str(root)
    header = f"Workplace root: {root_label}\nListing: {path_arg}\n"
    walk(target, 0)
    body = "\n".join(lines) if lines else "(empty)"
    if capped:
        body += f"\n… capped at {_MAX_ENTRIES} entries"
    return header + body


__all__ = ["run"]
