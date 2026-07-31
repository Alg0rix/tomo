"""Workplace-escape detection for file tools and bash/runpy."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.runtime.permissions.types import Finding

_FILE_PATH_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "list_dir",
        "delete_file",
        "search_files",
        "str_replace",
        "patch",
    }
)

_HOME_TOKEN_RE = re.compile(
    r"(?:~|\$\{?HOME\}?)(?:/[^\s;|&]*)?",
    re.IGNORECASE,
)
_ABS_PATH_RE = re.compile(r"(?<![\w./-])(/(?:[^\s;|&'\"]+))")
_CD_RE = re.compile(
    r"(?:^|[;&|\n])\s*cd\s+(?:--\s+)?(?P<path>[^\s;|&]+)",
    re.IGNORECASE,
)


def _under_root(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _resolve_user_path(raw: str, work_root: Path) -> Path | None:
    text = raw.strip().strip("'\"")
    if not text:
        return None
    try:
        if text.startswith("~"):
            return Path(text).expanduser().resolve()
        # $HOME / ${HOME}
        if text.startswith("$HOME") or text.startswith("${HOME}"):
            rest = text.split("}", 1)[-1] if text.startswith("${HOME}") else text[5:]
            if rest.startswith("/"):
                rest = rest[1:]
            return (Path.home() / rest).resolve() if rest else Path.home().resolve()
        p = Path(text)
        if p.is_absolute():
            return p.resolve()
        return (work_root / text).resolve()
    except OSError:
        return None


def _escape_finding(path: Path) -> Finding:
    # Prefer a stable home-relative key when under ~
    try:
        home = Path.home().resolve()
        rel = path.resolve().relative_to(home)
        key = f"escape:~/{rel.parts[0]}" if rel.parts else "escape:~"
    except ValueError:
        key = f"escape:{path.resolve().anchor}{path.resolve().parts[1] if len(path.resolve().parts) > 1 else ''}"
        # Simplify to first two components for allowlist grain
        parts = path.resolve().parts
        if len(parts) >= 2:
            key = f"escape:{parts[0]}{parts[1]}"
        else:
            key = f"escape:{path}"
    return Finding(
        kind="escape",
        key=key,
        description=f"path outside workplace: {path}",
        paths=(path.resolve(),),
    )


def detect_file_tool_escape(
    tool: str, args: dict[str, Any], work_root: Path
) -> list[Finding]:
    if tool not in _FILE_PATH_TOOLS:
        return []
    raw = args.get("path")
    if not isinstance(raw, str) or not raw.strip():
        return []
    target = _resolve_user_path(raw, work_root)
    if target is None:
        return []
    if _under_root(work_root, target):
        return []
    return [_escape_finding(target)]


def detect_shell_escape(command: str, work_root: Path) -> list[Finding]:
    if not isinstance(command, str) or not command.strip():
        return []
    findings: list[Finding] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        if _under_root(work_root, path):
            return
        f = _escape_finding(path)
        if f.key in seen:
            return
        seen.add(f.key)
        findings.append(f)

    for m in _HOME_TOKEN_RE.finditer(command):
        p = _resolve_user_path(m.group(0), work_root)
        if p is not None:
            _add(p)

    for m in _CD_RE.finditer(command):
        p = _resolve_user_path(m.group("path"), work_root)
        if p is not None:
            _add(p)

    for m in _ABS_PATH_RE.finditer(command):
        p = _resolve_user_path(m.group(1), work_root)
        if p is not None:
            _add(p)

    return findings


def detect_escape(
    tool: str, args: dict[str, Any], work_root: Path
) -> list[Finding]:
    if tool in _FILE_PATH_TOOLS:
        return detect_file_tool_escape(tool, args, work_root)
    if tool == "bash":
        cmd = args.get("command")
        if isinstance(cmd, str):
            return detect_shell_escape(cmd, work_root)
        return []
    if tool == "runpy":
        code = args.get("code")
        if isinstance(code, str):
            # Reuse shell heuristics on source text (open/Path literals with ~ or abs).
            return detect_shell_escape(code, work_root)
        return []
    return []


__all__ = ["detect_escape", "detect_file_tool_escape", "detect_shell_escape"]
