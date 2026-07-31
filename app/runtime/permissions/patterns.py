"""Hardline and dangerous command pattern detection (trimmed Hermes port)."""

from __future__ import annotations

import re

from app.runtime.permissions.types import Finding

_RE_FLAGS = re.IGNORECASE | re.DOTALL

_CMDPOS = (
    r"(?:^|[\n`]|\$\()"
    r"\s*"
    r"(?:sudo\s+(?:-[^\s]+\s+)*)?"
    r"(?:env\s+(?:\w+=\S*\s+)*)?"
    r"(?:(?:exec|nohup|setsid|time)\s+)*"
    r"\s*"
)

_HARDLINE_SYSTEM_DIRS = (
    r"/home|/home/\*|/root|/root/\*|/etc|/etc/\*|/usr|/usr/\*|"
    r"/var|/var/\*|/bin|/bin/\*|/sbin|/sbin/\*|/boot|/boot/\*|/lib|/lib/\*"
)


def _hardline_rm_path(path_alt: str, tail: str = r"(?:\s|$|[)`;|&])") -> str:
    return rf"(?:[\"'](?:{path_alt})[\"']|(?:{path_alt}){tail})"


_RM_FLAG_PREFIX = _CMDPOS + r"rm\s+(-[^\s]*\s+)*"

HARDLINE_PATTERNS: list[tuple[str, str]] = [
    (
        _RM_FLAG_PREFIX
        + _hardline_rm_path(r"/(?:(?:\.\.?)?/)*(?:\.\.?)?\**|/ \*"),
        "recursive delete of root filesystem",
    ),
    (
        _RM_FLAG_PREFIX + _hardline_rm_path(_HARDLINE_SYSTEM_DIRS),
        "recursive delete of system directory",
    ),
    (
        _RM_FLAG_PREFIX
        + _hardline_rm_path(r"(?:~|\$\{?HOME\}?)(?:/?|/\*)?"),
        "recursive delete of home directory",
    ),
    (r"\bmkfs(\.[a-z0-9]+)?\b", "format filesystem (mkfs)"),
    (
        r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*",
        "dd to raw block device",
    ),
    (r">\s*/dev/(sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*\b", "redirect to raw block device"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bkill\s+(-[^\s]+\s+)*-1\b", "kill all processes"),
    (_CMDPOS + r"(shutdown|reboot|halt|poweroff)\b", "system shutdown/reboot"),
    (_CMDPOS + r"init\s+[06]\b", "init 0/6 (shutdown/reboot)"),
    (
        _CMDPOS + r"systemctl\s+(poweroff|reboot|halt|kexec)\b",
        "systemctl poweroff/reboot",
    ),
]

DANGEROUS_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, stable_id, description)
    (r"\brm\s+(-[^\s]*\s+)*/", "delete_in_root", "delete in root path"),
    (r"\brm\s+-[^\s]*r", "recursive_delete", "recursive delete"),
    (r"\brm\s+--recursive\b", "recursive_delete", "recursive delete (long flag)"),
    (
        r"\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b",
        "world_writable",
        "world/other-writable permissions",
    ),
    (
        r"\b(curl|wget)\b.*\|\s*(?:[/\w]*/)?(?:ba)?sh(?:\s|$|-c)",
        "pipe_remote_shell",
        "pipe remote content to shell",
    ),
    (
        r"\b(bash|sh|zsh|ksh)\s+<\s*<?\s*\(\s*(curl|wget)\b",
        "process_subst_remote",
        "execute remote script via process substitution",
    ),
    (
        r"(?:>>?)\s*[~\"']?(?:/\$HOME|\$\{?HOME\}?|~)/\.(?:ssh|tomo|bashrc|zshrc|profile)",
        "sensitive_redirect",
        "overwrite sensitive home config via redirection",
    ),
    (
        r"\btee\b.*[~\"']?(?:/\$HOME|\$\{?HOME\}?|~)/\.(?:ssh|tomo)",
        "sensitive_tee",
        "overwrite sensitive file via tee",
    ),
    (r"\bDROP\s+(TABLE|DATABASE)\b", "sql_drop", "SQL DROP"),
    (
        r"\bDELETE\s+FROM\b(?![^\n]*\bWHERE\b)",
        "sql_delete_no_where",
        "SQL DELETE without WHERE",
    ),
    (r"\bpkill\s+-9\b", "pkill_force", "force kill processes"),
    (
        r"\bsystemctl\s+(-[^\s]+\s+)*(stop|restart|disable|mask)\b",
        "systemctl_disrupt",
        "stop/restart system service",
    ),
]

HARDLINE_COMPILED = [
    (re.compile(p, _RE_FLAGS), desc) for p, desc in HARDLINE_PATTERNS
]
DANGEROUS_COMPILED = [
    (re.compile(p, _RE_FLAGS), key, desc) for p, key, desc in DANGEROUS_PATTERNS
]


def detect_hardline(command: str) -> Finding | None:
    if not isinstance(command, str) or not command.strip():
        return None
    text = command.lower()
    for pattern_re, description in HARDLINE_COMPILED:
        if pattern_re.search(text):
            return Finding(
                kind="hardline",
                key=f"hardline:{description}",
                description=description,
            )
    return None


def detect_dangerous(command: str) -> Finding | None:
    if not isinstance(command, str) or not command.strip():
        return None
    text = command.lower()
    for pattern_re, key, description in DANGEROUS_COMPILED:
        if pattern_re.search(text):
            return Finding(
                kind="dangerous",
                key=f"dangerous:{key}",
                description=description,
            )
    return None


__all__ = [
    "detect_hardline",
    "detect_dangerous",
    "HARDLINE_PATTERNS",
    "DANGEROUS_PATTERNS",
]
