"""search_files tool — substring/regex search under the sandbox cwd."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from app.runtime.tools.sandbox import resolve_work_root
from app.runtime.tools.tunnel_rpc import try_tunnel_rpc

_MAX_MATCHES = 50
_MAX_SNIPPET = 200
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def run(arguments: dict[str, Any]) -> str:
    """Search files under the sandbox; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: search_files expects a dict of arguments"
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return "Error: 'pattern' argument must be a non-empty string"

    glob_pat = arguments.get("glob")
    if glob_pat is not None and not isinstance(glob_pat, str):
        return "Error: 'glob' argument must be a string"

    use_regex = bool(arguments.get("regex", False))
    remote = try_tunnel_rpc(
        "search_files",
        {
            "pattern": pattern,
            "glob": glob_pat or "",
            "regex": use_regex,
        },
    )
    if remote is not None:
        return remote

    if use_regex:
        try:
            cre = re.compile(pattern)
        except re.error as exc:
            return f"Error: invalid regex: {exc}"
    else:
        cre = None

    root = resolve_work_root()
    matches: list[str] = []
    try:
        for path in _iter_files(root, glob_pat):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = path.relative_to(root).as_posix()
            for lineno, line in enumerate(text.splitlines(), start=1):
                hit = False
                if cre is not None:
                    hit = cre.search(line) is not None
                else:
                    hit = pattern in line
                if not hit:
                    continue
                snippet = line.strip()
                if len(snippet) > _MAX_SNIPPET:
                    snippet = snippet[:_MAX_SNIPPET] + "…"
                matches.append(f"{rel}:{lineno}:{snippet}")
                if len(matches) >= _MAX_MATCHES:
                    break
            if len(matches) >= _MAX_MATCHES:
                break
    except OSError as exc:
        return f"Error: could not search files: {exc}"

    if not matches:
        return f"No matches for {pattern!r}"
    header = f"{len(matches)} match(es)"
    if len(matches) >= _MAX_MATCHES:
        header += f" (capped at {_MAX_MATCHES})"
    return header + "\n" + "\n".join(matches)


def _iter_files(root: Path, glob_pat: str | None):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if glob_pat and not fnmatch.fnmatch(path.name, glob_pat):
            continue
        yield path


__all__ = ["run"]
