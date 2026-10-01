"""Telegram delivery bound to a turn, inherited by its background task only."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.channels.telegram_ui import TelegramTurnUI

_turn: ContextVar[TelegramTurnUI | None] = ContextVar("telegram_turn", default=None)


def bind_turn(ui: TelegramTurnUI | None) -> Token:
    return _turn.set(ui)


def reset_turn(token: Token) -> None:
    _turn.reset(token)


def current_turn(session_id: str | None = None) -> TelegramTurnUI | None:
    ui = _turn.get()
    if ui is not None and (session_id is None or ui.session_id == session_id):
        return ui
    return None
