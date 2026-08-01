"""Path jail helpers for CodeQL ``py/path-injection``.

Uses ``realpath`` + prefix check (``startswith``) — the pattern CodeQL
treats as a validated path before FS sinks.
"""

from __future__ import annotations

import os
from pathlib import Path


def ensure_under(root: Path | str, candidate: Path | str) -> Path:
    """Return ``candidate`` when it resolves under ``root``.

    Raises ``ValueError`` when the path escapes the root (including via
    symlinks after resolve).
    """
    root_r = os.path.realpath(os.path.expanduser(str(root)))
    raw = os.path.expanduser(str(candidate))
    if not os.path.isabs(raw):
        raw = os.path.join(root_r, raw)
    target = os.path.realpath(raw)
    # Trailing sep so "/tmp/foo" does not match root "/tmp/fo"
    root_prefix = root_r if root_r.endswith(os.sep) else root_r + os.sep
    if target != root_r and not target.startswith(root_prefix):
        raise ValueError(f"path escapes root ({root_r}): {candidate}")
    return Path(target)


def try_under(root: Path | str, candidate: Path | str) -> Path | None:
    """Like :func:`ensure_under` but returns ``None`` on escape / OSError."""
    try:
        return ensure_under(root, candidate)
    except (ValueError, OSError):
        return None
