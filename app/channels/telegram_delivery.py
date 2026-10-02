"""Telegram implementation of the scheduled delivery contract."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from typing import Any

from app.channels.delivery import DeliveryBlocked
from app.channels.telegram import TelegramAPI, chat_is_allowed, user_id_for_chat
from app.channels.telegram_context import bind_turn, current_turn, reset_turn
from app.channels.telegram_ui import TelegramTurnUI
from app.services.store import store


def _bot_identity(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _check_target(target: dict[str, Any]) -> str:
    settings = store.get_settings()
    token = str(settings.get("telegram_bot_token") or "").strip()
    if (
        not settings.get("telegram_enabled")
        or not token
        or target.get("bot") != _bot_identity(token)
        or not isinstance(target.get("chat_id"), int)
        or not chat_is_allowed(target["chat_id"])
        or (target.get('user_id') is not None and target['user_id'] != user_id_for_chat(target['chat_id']))
    ):
        raise DeliveryBlocked(
            "Telegram destination revoked, disabled, or bot identity changed"
        )
    return token


class ScheduledTelegramAPI(TelegramAPI):
    """Check current authorization before every text/file request, not just at boot."""

    def __init__(self, target: dict[str, Any]) -> None:
        self.target = target
        self._final_attempts: int | None = None
        super().__init__(_check_target(target), authorize=self._check_access)

    def _check_access(self) -> None:
        _check_target(self.target)
        if self._final_attempts is not None:
            self._final_attempts += 1

    async def send_final(self, content: str, *, delivery_id: str) -> dict[str, Any]:
        # Telegram does not accept an idempotency key; uncertain sends stay unknown.
        self._final_attempts = 0
        try:
            result = await self.send_answer(
                self.target["chat_id"],
                content,
                thread_id=self.target.get("thread_id"),
            )
        except DeliveryBlocked:
            if self._final_attempts:
                raise RuntimeError(
                    "Telegram final delivery stopped; may be partially delivered"
                ) from None
            raise
        finally:
            self._final_attempts = None
        if not isinstance(result, dict) or not result.get("message_id"):
            raise RuntimeError("Telegram did not confirm final delivery")
        return {"message_id": result["message_id"]}


class TelegramDeliveryChannel:
    def capture_current(self) -> dict[str, Any] | None:
        from app.runtime.artifacts.fs import current_session_id

        sid = current_session_id()
        ui = current_turn(sid) if sid else None
        if ui is None:
            return None
        if ui.finished or ui.stop_requested or not chat_is_allowed(ui.chat_id):
            raise DeliveryBlocked("Telegram turn is no longer authorized")
        session = store.get_session(ui.session_id)
        owner = ((ui.api.target.get('user_id') or user_id_for_chat(ui.chat_id))
                 if isinstance(ui.api, ScheduledTelegramAPI)
                 else session['user_id'] if session else None)
        if owner != user_id_for_chat(ui.chat_id):
            raise DeliveryBlocked('Telegram account link changed')
        return {
            "user_id": owner,
            "chat_id": ui.chat_id,
            "thread_id": ui.thread_id,
            "actor_id": ui.actor_id,
            "reply_to": ui.reply_to,
            "bot": _bot_identity(ui.api._token),
        }

    @asynccontextmanager
    async def open(self, target: dict[str, Any], session_id: str):
        # Scheduled jobs deliberately own isolated scheduler:<job> sessions.
        # Authorization follows the captured account, not that execution id.
        api = ScheduledTelegramAPI(target)
        ui = TelegramTurnUI(
            api, target["chat_id"], session_id, thread_id=target.get("thread_id")
        )
        binding = bind_turn(ui)
        try:
            yield api
        finally:
            ui.finished = True
            reset_turn(binding)
            await api.aclose()
