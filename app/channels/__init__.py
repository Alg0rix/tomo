"""Channel adapters — web, Telegram, WhatsApp, and more."""

from __future__ import annotations

from typing import Any


def telegram_status(settings: dict[str, Any] | None = None) -> str:
    """Re-export without importing telegram at package import time.

    Eager ``from app.channels.telegram import …`` would cycle:
    ``chat → channels.web → channels → telegram → chat``.
    """
    from app.channels.telegram import telegram_status as _status

    return _status(settings)


__all__ = ["telegram_status"]
