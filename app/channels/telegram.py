"""Telegram bot channel — long-poll getUpdates → session turn → reply.

Token lives in settings (``telegram_bot_token``, Fernet at rest). Never log the
token. Inbound text maps ``chat_id`` → ``user_id=tg_<chat_id>`` session via the
coordinator agent, then reuses :func:`app.services.chat.run_session_turn`.

HTTP goes through :mod:`httpx` so tests inject ``MockTransport`` (no network).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

from app.services.store import store

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"


def telegram_status(settings: dict[str, Any] | None = None) -> str:
    """Honest channel status: ``connected``, ``needs_token``, or ``off``."""
    s = settings if settings is not None else store.get_settings()
    token = str(s.get("telegram_bot_token") or "").strip()
    enabled = bool(s.get("telegram_enabled"))
    if not token:
        return "needs_token"
    if enabled:
        return "connected"
    return "off"


def user_id_for_chat(chat_id: int | str) -> str:
    """Stable Tomo user id for a Telegram chat."""
    return f"tg_{chat_id}"


def extract_text_message(update: dict[str, Any]) -> tuple[int, str] | None:
    """Return ``(chat_id, text)`` from a Bot API update, or ``None``."""
    msg = update.get("message") or update.get("edited_message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    text = msg.get("text")
    if chat_id is None or not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return int(chat_id), stripped
    except (TypeError, ValueError):
        return None


class TelegramAPI:
    """Thin Telegram Bot API client (getUpdates / sendMessage)."""

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        raw = (token or "").strip()
        if not raw:
            raise ValueError("Telegram bot token is required")
        self._token = raw
        self._owns_client = client is None
        # Long-poll getUpdates uses timeout up to ~30s plus slack.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    def _url(self, method: str) -> str:
        # Token only in path — never log this URL.
        return f"{API_ROOT}/bot{self._token}/{method}"

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 25,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "limit": limit}
        if offset is not None:
            payload["offset"] = offset
        resp = await self._client.post(self._url("getUpdates"), json=payload)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram getUpdates failed: {body.get('description')}")
        result = body.get("result") or []
        return [u for u in result if isinstance(u, dict)]

    async def send_message(self, chat_id: int | str, text: str) -> dict[str, Any]:
        payload = {"chat_id": chat_id, "text": text}
        resp = await self._client.post(self._url("sendMessage"), json=payload)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {body.get('description')}")
        result = body.get("result")
        return result if isinstance(result, dict) else {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def run_channel_turn(session_id: str, message: str) -> str:
    """Run the web turn pipeline; return the latest final (or error) text."""
    # Lazy import: chat → channels.web → channels package must not pull telegram
    # at import time (circular with this module).
    from app.services.chat import run_session_turn

    async with contextlib.aclosing(
        run_session_turn(session_id, message, "telegram")
    ) as agen:
        async for _chunk in agen:
            pass
    history = store.get_session_history(session_id)
    for entry in reversed(history):
        kind = entry.get("type")
        if kind in ("final", "error") and entry.get("content"):
            return str(entry["content"])
    return ""


def _resolve_agent_id(agent_id: str | None = None) -> str | None:
    if agent_id:
        return agent_id if store.get_agent(agent_id) else None
    coord = store.get_coordinator()
    return coord["id"] if coord else None


async def handle_inbound_text(
    chat_id: int | str,
    text: str,
    *,
    api: TelegramAPI | None = None,
    agent_id: str | None = None,
    send_reply: bool = True,
) -> dict[str, Any]:
    """Map chat → session, run one turn, optionally reply on Telegram.

    Returns ``{"session_id", "reply", "agent_id"}``.
    """
    resolved = _resolve_agent_id(agent_id)
    if not resolved:
        raise ValueError("No agent available for Telegram turns")
    user_id = user_id_for_chat(chat_id)
    session_id = store.get_or_create_session(resolved, user_id)
    logger.info(
        "telegram inbound chat_id=%s session_id=%s agent_id=%s chars=%s",
        chat_id,
        session_id,
        resolved,
        len(text or ""),
    )
    reply = await run_channel_turn(session_id, text)
    if send_reply and reply and api is not None:
        await api.send_message(chat_id, reply)
    return {"session_id": session_id, "reply": reply, "agent_id": resolved}


async def process_update(
    update: dict[str, Any],
    *,
    api: TelegramAPI | None = None,
    agent_id: str | None = None,
    send_reply: bool = True,
) -> dict[str, Any] | None:
    """Handle one Bot API update; return handle result or ``None`` if ignored."""
    extracted = extract_text_message(update)
    if not extracted:
        return None
    chat_id, text = extracted
    return await handle_inbound_text(
        chat_id,
        text,
        api=api,
        agent_id=agent_id,
        send_reply=send_reply,
    )


async def poll_once(
    api: TelegramAPI,
    *,
    offset: int = 0,
    timeout: int = 25,
    agent_id: str | None = None,
) -> int:
    """Fetch and process one getUpdates batch. Returns next offset."""
    updates = await api.get_updates(offset=offset or None, timeout=timeout)
    next_offset = offset
    for update in updates:
        uid = update.get("update_id")
        if isinstance(uid, int):
            next_offset = max(next_offset, uid + 1)
        try:
            await process_update(update, api=api, agent_id=agent_id, send_reply=True)
        except Exception:
            logger.exception("telegram update failed update_id=%s", uid)
    return next_offset


_supervisor_task: asyncio.Task[None] | None = None
_supervisor_stop: asyncio.Event | None = None


async def _supervisor_loop(stop: asyncio.Event) -> None:
    """Background long-poll: idle when disabled / no token; poll when ready."""
    offset = 0
    while not stop.is_set():
        settings = store.get_settings()
        token = str(settings.get("telegram_bot_token") or "").strip()
        enabled = bool(settings.get("telegram_enabled"))
        if not (enabled and token):
            try:
                await asyncio.wait_for(stop.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            continue
        api = TelegramAPI(token)
        try:
            offset = await poll_once(api, offset=offset, timeout=25)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("telegram poll error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        finally:
            await api.aclose()


def start_telegram_supervisor() -> None:
    """Start the long-poll supervisor (idempotent). Called from app lifespan."""
    global _supervisor_task, _supervisor_stop
    if _supervisor_task is not None and not _supervisor_task.done():
        return
    _supervisor_stop = asyncio.Event()
    _supervisor_task = asyncio.create_task(
        _supervisor_loop(_supervisor_stop), name="telegram-supervisor"
    )
    logger.info("telegram supervisor started")


async def stop_telegram_supervisor() -> None:
    """Stop the long-poll supervisor (idempotent)."""
    global _supervisor_task, _supervisor_stop
    if _supervisor_stop is not None:
        _supervisor_stop.set()
    task = _supervisor_task
    _supervisor_task = None
    _supervisor_stop = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        logger.info("telegram supervisor stopped")


__all__ = [
    "TelegramAPI",
    "extract_text_message",
    "handle_inbound_text",
    "poll_once",
    "process_update",
    "run_channel_turn",
    "start_telegram_supervisor",
    "stop_telegram_supervisor",
    "telegram_status",
    "user_id_for_chat",
]
