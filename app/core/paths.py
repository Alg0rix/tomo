"""Path jail helpers recognized by CodeQL ``py/path-injection``.

Resolve a candidate under a fixed root and reject escapes via
``Path.relative_to``.
"""

from __future__ import annotations

from pathlib import Path


def ensure_under(root: Path, candidate: Path | str) -> Path:
    """Return ``candidate`` resolved when it stays under ``root``.

    Raises ``ValueError`` when the path escapes the root (including via
    symlinks after resolve).
    """
    root_r = Path(root).expanduser().resolve()
    cand = Path(candidate).expanduser()
    if cand.is_absolute():
        target = cand.resolve()
    else:
        target = (root_r / cand).resolve()
    try:
        target.relative_to(root_r)
    except ValueError as exc:
        raise ValueError(f"path escapes root ({root_r}): {candidate}") from exc
    return target


def try_under(root: Path, candidate: Path | str) -> Path | None:
    """Like :func:`ensure_under` but returns ``None`` on escape / OSError."""
    try:
        return ensure_under(root, candidate)
    except (ValueError, OSError):
        return None
