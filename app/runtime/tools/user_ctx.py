"""Turn-scoped login account id for tools and memory isolation.

Bound for the duration of ``run_turn`` (and learning review) so tools like
``session_search`` and vault memory can scope
to the session owner without every call site threading ``user_id``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_user_id: ContextVar[str | None] = ContextVar("tool_user_id", default=None)



def bind_user(user_id: str | None) -> Token:
    """Bind the account id for the current agent turn / review."""
    from app.runtime.access import AccessDenied
    uid = (user_id or "").strip()
    if not uid:
        raise AccessDenied("User identity is required")
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
    """Current validated execution owner, never a coordinator/web default."""
    from app.runtime.access import current_execution, AccessDenied
    context = current_execution(required=False)
    if context:
        from app.services import store
        return store.access.revalidate(context).user_id
    raw = _user_id.get()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    raise AccessDenied("User identity is required")


__all__ = ["bind_user", "reset_user", "current_user_id"]
