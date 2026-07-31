"""Combine hardline, deny, dangerous, and escape findings for a tool call."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from app.runtime.permissions.escape import detect_escape
from app.runtime.permissions.patterns import detect_dangerous, detect_hardline
from app.runtime.permissions.types import Assessment, Finding


def _match_deny(command: str, deny_globs: list[str]) -> Finding | None:
    text = command.strip()
    if not text:
        return None
    lower = text.lower()
    for pattern in deny_globs:
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        p = pattern.strip()
        if fnmatch.fnmatchcase(lower, p.lower()):
            return Finding(
                kind="user_deny",
                key=f"user_deny:{p}",
                description=f"matches deny rule '{p}'",
            )
    return None


def _commandish(tool: str, args: dict[str, Any]) -> str:
    if tool == "bash":
        c = args.get("command")
        return c if isinstance(c, str) else ""
    if tool == "runpy":
        c = args.get("code")
        return c if isinstance(c, str) else ""
    if tool in {
        "read_file",
        "write_file",
        "list_dir",
        "delete_file",
        "search_files",
        "str_replace",
        "patch",
    }:
        p = args.get("path")
        return f"{tool}:{p}" if isinstance(p, str) else tool
    return tool


def assess(
    tool: str,
    args: dict[str, Any] | None,
    work_root: Path,
    deny_globs: list[str] | None = None,
) -> Assessment:
    """Return findings for ``tool`` / ``args`` relative to ``work_root``."""
    arguments = args if isinstance(args, dict) else {}
    findings: list[Finding] = []

    cmd = ""
    if tool == "bash":
        raw = arguments.get("command")
        cmd = raw if isinstance(raw, str) else ""
    elif tool == "runpy":
        raw = arguments.get("code")
        cmd = raw if isinstance(raw, str) else ""

    if cmd:
        hard = detect_hardline(cmd)
        if hard is not None:
            findings.append(hard)

    deny = _match_deny(_commandish(tool, arguments), list(deny_globs or []))
    if deny is not None:
        findings.append(deny)

    # Escape + dangerous only matter if not already hardline-blocked for messaging,
    # but we still collect them for allowlist keys when hardline is absent.
    if not any(f.kind == "hardline" for f in findings):
        findings.extend(detect_escape(tool, arguments, work_root))
        if cmd:
            dang = detect_dangerous(cmd)
            if dang is not None:
                findings.append(dang)

    return Assessment(findings=findings)


__all__ = ["assess"]
