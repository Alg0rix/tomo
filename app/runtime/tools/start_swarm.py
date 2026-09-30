"""Validate a chat handoff; the web runtime owns actual swarm execution."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_request: ContextVar[str | None] = ContextVar("start_swarm_user_request", default=None)
ACCEPTED = "Swarm handoff accepted"


def bind_context(user_request: str) -> Token:
    return _request.set(user_request)


def reset_context(token: Token) -> None:
    try:
        _request.reset(token)
    except ValueError:
        _request.set(None)


def run(arguments: dict[str, Any]) -> str:
    from app.runtime.agent.subagent import current_depth

    user_request = _request.get()
    if user_request is None or current_depth() > 0:
        return "Error: start_swarm is available only in the root chat turn"
    request = arguments.get("request")
    quote = arguments.get("consent_quote")
    if not isinstance(request, str) or not request.strip():
        return "Error: provide the complete user task"
    if not isinstance(quote, str) or not quote.strip() or quote.casefold() not in user_request.casefold():
        return "Error: consent_quote must quote the current user's explicit request or approval; ask for approval if absent"
    return ACCEPTED
