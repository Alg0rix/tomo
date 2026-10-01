"""Scheduled output contract. Channels own addressing, access, and transport.

Register a provider to add a channel; neither scheduler nor SQLite knows its
address format. Targets are server-captured JSON, never model-supplied recipients.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncContextManager, Protocol

_scheduled_session: ContextVar[str | None] = ContextVar(
    "scheduled_delivery_session", default=None
)


def is_scheduled_delivery(session_id: str) -> bool:
    return _scheduled_session.get() == session_id


class DeliveryBlocked(RuntimeError):
    """Destination is no longer authorized; no send was attempted."""


class BoundDelivery(Protocol):
    async def send_final(self, content: str, *, delivery_id: str) -> dict[str, Any]: ...


class DeliveryChannel(Protocol):
    def capture_current(self) -> dict[str, Any] | None: ...
    def open(
        self, target: dict[str, Any], session_id: str
    ) -> AsyncContextManager[BoundDelivery]: ...


_channels: dict[str, DeliveryChannel] = {}


def register_delivery_channel(name: str, channel: DeliveryChannel) -> None:
    if not name or name in _channels:
        raise ValueError(f"Delivery channel already registered or invalid: {name!r}")
    _channels[name] = channel


def _load_builtin() -> None:
    if "telegram" not in _channels:
        from app.channels.telegram_delivery import TelegramDeliveryChannel

        # Tool capture can run concurrently in worker threads.
        _channels.setdefault("telegram", TelegramDeliveryChannel())


def capture_current_target() -> dict[str, Any] | None:
    _load_builtin()
    for name, channel in _channels.items():
        target = channel.capture_current()
        if target is not None:
            return {**target, "version": 1, "channel": name}
    return None


@asynccontextmanager
async def open_delivery(target: dict[str, Any], session_id: str):
    _load_builtin()
    if target.get("version") != 1:
        raise DeliveryBlocked("Unsupported delivery target version")
    channel = _channels.get(target.get("channel"))
    if channel is None:
        raise DeliveryBlocked("Delivery channel is not registered")
    binding = _scheduled_session.set(session_id)
    try:
        async with channel.open(target, session_id) as delivery:
            yield delivery
    finally:
        _scheduled_session.reset(binding)
