"""Turn-scoped login account id for tools and memory isolation.

Bound for the duration of ``run_turn`` (and learning review) so tools like
``session_search``, ``remember``/``recall``, and curated ``USER.md`` can scope
to the session owner without every call site threading ``user_id``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_user_id: ContextVar[str | None] = ContextVar("tool_user_id", default=None)

_DEFAULT = "web"


def bind_user(user_id: str | None) -> Token:
    """Bind the account id for the current agent turn / review."""
    uid = (user_id or "").strip() or _DEFAULT
    return _user_id.set(uid)


def reset_user(token: Token | None = None) -> None:
    """Clear or reset the bound user id (async-generator safe)."""
    if token is not None:
        try:
            _user_id.reset(token)
        except ValueError:
            _user_id.set(None)
    else:
        _user_id.set(None)


def current_user_id() -> str:
    """Authenticated account for this turn, or ``web`` when unbound."""
    raw = _user_id.get()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return _DEFAULT


__all__ = ["bind_user", "reset_user", "current_user_id"]
