"""Per-call outside-workplace path grants for jail_path."""

from __future__ import annotations

from contextvars import ContextVar, Token
from pathlib import Path
from typing import Literal

OutsideGrant = frozenset[Path] | Literal["*"] | None

_outside_grant: ContextVar[OutsideGrant] = ContextVar(
    "permissions_outside_grant", default=None
)


def set_outside_grant(grant: OutsideGrant) -> Token:
    return _outside_grant.set(grant)


def reset_outside_grant(token: Token | None = None) -> None:
    if token is not None:
        try:
            _outside_grant.reset(token)
        except ValueError:
            _outside_grant.set(None)
    else:
        _outside_grant.set(None)


def current_outside_grant() -> OutsideGrant:
    return _outside_grant.get()


def path_allowed_by_grant(target: Path, grant: OutsideGrant) -> bool:
    if grant is None:
        return False
    if grant == "*":
        return True
    try:
        resolved = target.resolve()
    except OSError:
        return False
    for allowed in grant:
        try:
            allowed_r = allowed.resolve()
        except OSError:
            continue
        if resolved == allowed_r:
            return True
        try:
            resolved.relative_to(allowed_r)
            return True
        except ValueError:
            continue
    return False


__all__ = [
    "OutsideGrant",
    "set_outside_grant",
    "reset_outside_grant",
    "current_outside_grant",
    "path_allowed_by_grant",
]
