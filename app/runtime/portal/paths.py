"""Portal path helpers — coordinator staging under ``$TOMO_WORK/_portal/<name>/``.

Agents address portals as ``/_portal/<name>/relative/path``. Physical files live
on the Tomo host so any workplace can push/pull through the coordinator.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core import config

_PORTAL_PREFIX = "/_portal/"
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def portal_base(*, work_root: Path | None = None) -> Path:
    root = Path(work_root) if work_root is not None else config.TOMO_WORK
    return root / "_portal"


def is_portal_path(path: str) -> bool:
    text = (path or "").strip().replace("\\", "/")
    return text.startswith("/_portal/") or text == "/_portal"


def parse_portal_path(path: str) -> tuple[str, str]:
    """Return ``(portal_name, relative_path)`` from a ``/_portal/...`` path."""
    text = (path or "").strip().replace("\\", "/")
    if text == "/_portal" or text == "/_portal/":
        raise ValueError("portal path needs a name: /_portal/<name>/...")
    if not text.startswith(_PORTAL_PREFIX):
        raise ValueError(f"not a portal path: {path!r}")
    rest = text[len(_PORTAL_PREFIX) :].lstrip("/")
    if not rest:
        raise ValueError("portal path needs a name: /_portal/<name>/...")
    name, _, rel = rest.partition("/")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"invalid portal name {name!r} "
            "(use letters, digits, ._- ; max 64 chars)"
        )
    rel = rel.lstrip("/")
    if ".." in Path(rel).parts:
        raise ValueError("path traversal is not allowed in portal paths")
    return name, rel


def resolve_portal_fs(
    path: str, *, work_root: Path | None = None, create: bool = False
) -> Path:
    """Map ``/_portal/<name>/...`` to an absolute filesystem path."""
    name, rel = parse_portal_path(path)
    base = portal_base(work_root=work_root) / name
    if create:
        base.mkdir(parents=True, exist_ok=True)
    target = (base / rel).resolve() if rel else base.resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError("path escapes portal root") from exc
    if create and rel:
        target.parent.mkdir(parents=True, exist_ok=True)
    return target


def list_portals(*, work_root: Path | None = None) -> list[dict[str, str]]:
    base = portal_base(work_root=work_root)
    if not base.is_dir():
        return []
    out: list[dict[str, str]] = []
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and _NAME_RE.match(entry.name):
            out.append({"name": entry.name, "path": f"/_portal/{entry.name}"})
    return out


__all__ = [
    "portal_base",
    "is_portal_path",
    "parse_portal_path",
    "resolve_portal_fs",
    "list_portals",
]
