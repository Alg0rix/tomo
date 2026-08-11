"""Turn-scoped browser user binding (ContextVar).

Similar to :mod:`app.runtime.tools.sandbox` agent bind — tool backends read
the current Tomo user id so the gateway can route to the right browser
session without threading user_id through every tool argument.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_browser_user_id: ContextVar[str | None] = ContextVar(
    "browser_user_id", default=None
)
_browser_session_id: ContextVar[str | None] = ContextVar(
    "browser_chat_session_id", default=None
)
_browser_agent_id: ContextVar[str | None] = ContextVar(
    "browser_agent_id", default=None
)


def bind_browser_user(user_id: str | None) -> Token:
    return _browser_user_id.set(
        user_id if isinstance(user_id, str) and user_id.strip() else None
    )


def reset_browser_user(token: Token | None = None) -> None:
    if token is not None:
        try:
            _browser_user_id.reset(token)
        except ValueError:
            _browser_user_id.set(None)
    else:
        _browser_user_id.set(None)


def current_browser_user_id() -> str | None:
    return _browser_user_id.get()


def bind_browser_chat_session(session_id: str | None) -> Token:
    return _browser_session_id.set(
        session_id if isinstance(session_id, str) and session_id.strip() else None
    )


def reset_browser_chat_session(token: Token | None = None) -> None:
    if token is not None:
        try:
            _browser_session_id.reset(token)
        except ValueError:
            _browser_session_id.set(None)
    else:
        _browser_session_id.set(None)


def current_browser_chat_session() -> str | None:
    return _browser_session_id.get()


def bind_browser_agent(agent_id: str | None) -> Token:
    return _browser_agent_id.set(
        agent_id if isinstance(agent_id, str) and agent_id.strip() else None
    )


def reset_browser_agent(token: Token | None = None) -> None:
    if token is not None:
        try:
            _browser_agent_id.reset(token)
        except ValueError:
            _browser_agent_id.set(None)
    else:
        _browser_agent_id.set(None)


def current_browser_agent_id() -> str | None:
    return _browser_agent_id.get()
