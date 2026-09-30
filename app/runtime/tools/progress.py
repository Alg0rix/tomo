"""Live output sink for long-running tools (bash streams chunks through it).

The agent loop binds a sink before running a streamable tool; the tool
captures it with :func:`current` (worker threads do not inherit contextvars)
and calls it with text chunks as they arrive. No sink bound → no-op.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Callable

Sink = Callable[[str], None]

_sink: ContextVar[Sink | None] = ContextVar("tomo_tool_progress", default=None)


def bind(sink: Sink | None) -> Token:
    return _sink.set(sink)


def reset(token: Token) -> None:
    _sink.reset(token)


def current() -> Sink | None:
    return _sink.get()


__all__ = ["Sink", "bind", "current", "reset"]
