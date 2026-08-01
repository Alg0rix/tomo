"""Path jail helpers for CodeQL ``py/path-injection``.

Validate with ``os.path.realpath`` + ``commonpath`` (recognized sanitizer)
before returning a ``Path``.
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
    try:
        common = os.path.commonpath([root_r, target])
    except ValueError as exc:
        raise ValueError(f"path escapes root ({root_r}): {candidate}") from exc
    if common != root_r:
        raise ValueError(f"path escapes root ({root_r}): {candidate}")
    return Path(target)


def try_under(root: Path | str, candidate: Path | str) -> Path | None:
    """Like :func:`ensure_under` but returns ``None`` on escape / OSError."""
    try:
        return ensure_under(root, candidate)
    except (ValueError, OSError):
        return None
