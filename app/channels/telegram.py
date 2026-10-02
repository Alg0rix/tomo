"""Telegram bot channel — long-poll getUpdates → session turn → reply.

Token lives in settings (``telegram_bot_token``, Fernet at rest). Never log the
token. Inbound chats use a linked account (or ``tg_<chat_id>`` when unlinked),
with a separate delivery destination. Turns reuse the web background manager.

HTTP goes through :mod:`httpx` so tests inject ``MockTransport`` (no network).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import mimetypes
from typing import TYPE_CHECKING, Any, Callable

import httpx

from app.services.store import store

logger = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"

if TYPE_CHECKING:
    from app.channels.telegram_ui import TelegramTurnUI


def telegram_status(settings: dict[str, Any] | None = None) -> str:
    """Configuration status (``connected`` means enabled, not a network probe)."""
    s = settings if settings is not None else store.get_settings()
    token = str(s.get("telegram_bot_token") or "").strip()
    enabled = bool(s.get("telegram_enabled"))
    if not token:
        return "needs_token"
    if enabled:
        return "connected"
    return "off"


def user_id_for_chat(chat_id: int | str) -> str:
    """Stable account id when linked, otherwise the chat's isolated identity."""
    from app.services.telegram_accounts import user_id_for_chat as resolve

    return resolve(chat_id)


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
    if not isinstance(text, str) and isinstance(msg.get("rich_message"), dict):
        from app.channels.telegram_format import rich_plain_text

        text = rich_plain_text(msg["rich_message"].get("blocks", []))
    if chat_id is None or not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return int(chat_id), stripped
    except (TypeError, ValueError):
        return None


class TelegramAPIError(RuntimeError):
    """Bot API failure without a token-bearing URL in its exception text."""

    def __init__(self, code: int, description: str) -> None:
        self.code = code
        self.description = description
        super().__init__(f"Telegram API error ({code})")


class TelegramAPI:
    """Bot API transport; bounded flood retries and safe formatting fallback."""

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
        authorize: Callable[[], None] | None = None,
    ) -> None:
        raw = (token or "").strip()
        if not raw:
            raise ValueError("Telegram bot token is required")
        self._token = raw
        self._authorize = authorize
        self._rich_disabled = False
        self._draft_disabled = False
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    def _url(self, method: str) -> str:
        return f"{API_ROOT}/bot{self._token}/{method}"

    async def _request(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        retry_flood: bool = True,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> Any:
        request_body = (
            {"json": payload}
            if files is None
            else {
                "data": {
                    key: json.dumps(value)
                    if isinstance(value, (dict, list, bool))
                    else str(value)
                    for key, value in payload.items()
                },
                "files": files,
            }
        )
        for attempt in range(3):
            if self._authorize is not None:
                self._authorize()
            try:
                response = await self._client.post(
                    self._url(method),
                    **request_body,
                    timeout=60.0 if method == "getUpdates" or files is not None else 10.0,
                )
                body = response.json()
            except (httpx.HTTPError, ValueError):
                raise RuntimeError("Telegram network request failed") from None
            if body.get("ok"):
                return body.get("result")
            code = body.get("error_code") or response.status_code
            if code == 429 and attempt < 2 and retry_flood:
                delay = float((body.get("parameters") or {}).get("retry_after") or 1)
                if 0 < delay <= 30:
                    await asyncio.sleep(delay)
                    continue
            raise TelegramAPIError(int(code), str(body.get("description") or ""))
        return None

    async def get_updates(
        self, *, offset: int | None = None, timeout: int = 25, limit: int = 100
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "limit": limit,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._request("getUpdates", payload)
        return [u for u in (result or []) if isinstance(u, dict)]

    async def download_file(self, file_id: str, *, max_bytes: int) -> bytes:
        from pathlib import PurePosixPath
        from app.channels.telegram_media import MediaError

        try:
            info = await self._request("getFile", {"file_id": file_id})
        except (TelegramAPIError, RuntimeError):
            raise MediaError(
                "I couldn't retrieve this file from Telegram. Please resend it."
            ) from None
        path = str((info or {}).get("file_path") or "")
        if (
            not path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or any(c in path for c in "\\%?#:")
            or PurePosixPath(path).is_absolute()
        ):
            raise MediaError("Telegram did not provide a valid file. Please resend it.")
        if int(info.get("file_size") or 0) > max_bytes:
            raise MediaError("This file is too large. Send a file smaller than 20 MB.")
        try:
            async with self._client.stream(
                "GET",
                f"{API_ROOT}/file/bot{self._token}/{path}",
                timeout=60,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(data) + len(chunk) > max_bytes:
                        raise MediaError(
                            "This file is too large. Send a file smaller than 20 MB."
                        )
                    data.extend(chunk)
                if not data:
                    raise MediaError("Telegram sent an empty file. Please resend it.")
                return bytes(data)
        except httpx.HTTPError:
            raise MediaError(
                "I couldn't download this file from Telegram. Please resend it."
            ) from None

    @property
    def rich_enabled(self) -> bool:
        return (
            bool(store.get_settings().get("telegram_rich_messages"))
            and not self._rich_disabled
        )

    async def _rich_request(self, method: str, payload: dict) -> Any:
        try:
            return await self._request(method, payload)
        except TelegramAPIError as exc:
            if exc.code == 400 and "message is not modified" in exc.description.lower():
                return {}
            # Only a definite rejection is safe to retry with legacy formatting.
            if exc.code not in {400, 404, 501}:
                raise
            if exc.code in {404, 501} or "method" in exc.description.lower():
                self._rich_disabled = True
            return None

    async def send_file(
        self,
        chat_id: int | str,
        filename: str,
        data: bytes,
        *,
        caption: str = "",
        kind: str = "auto",
        thread_id: int | None = None,
        reply_to: int | None = None,
        silent: bool = False,
    ) -> dict:
        from pathlib import Path

        if kind not in {"auto", "photo", "document"}:
            raise ValueError("kind must be auto, photo, or document")
        photo = kind == "photo" or (
            kind == "auto"
            and Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg"}
            and len(data) <= 10_000_000
        )
        limit = 10_000_000 if photo else 50_000_000
        if not data or len(data) > limit:
            raise ValueError(f"File must be nonempty and at most {limit} bytes")
        if len(caption) > 1024:
            raise ValueError("caption must be at most 1024 characters")
        field = "photo" if photo else "document"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "caption": caption,
            "disable_notification": silent,
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if reply_to is not None:
            payload["reply_parameters"] = {
                "message_id": reply_to,
                "allow_sending_without_reply": True,
            }
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return (
            await self._request(
                "sendPhoto" if photo else "sendDocument",
                payload,
                files={field: (filename, data, mime)},
            )
            or {}
        )

    async def send_answer(
        self, chat_id: int | str, text: str, *, preview: bool = False, **kwargs
    ) -> dict:
        from app.channels.telegram_format import (
            render_rich_html,
            render_markdown,
            split_html,
        )

        if self.rich_enabled:
            payload = {
                "chat_id": chat_id,
                "rich_message": {"html": render_rich_html(text)},
                "disable_notification": kwargs.get("silent", False),
            }
            if kwargs.get("thread_id") is not None:
                payload["message_thread_id"] = kwargs["thread_id"]
            if kwargs.get("reply_to") is not None:
                payload["reply_parameters"] = {
                    "message_id": kwargs["reply_to"],
                    "allow_sending_without_reply": True,
                }
            result = await self._rich_request("sendRichMessage", payload)
            if result is not None:
                return result
        if preview:
            chunks = split_html(render_markdown(text), limit=3600)
            return await self.send_html(chat_id, chunks[0] if chunks else "…", **kwargs)
        return await self.send_message(chat_id, text, formatted=True, **kwargs)

    async def edit_answer(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        thread_id=None,
        preview: bool = False,
    ) -> dict:
        from app.channels.telegram_format import (
            render_rich_html,
            render_markdown,
            split_html,
        )

        if self.rich_enabled:
            result = await self._rich_request(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "rich_message": {"html": render_rich_html(text)},
                },
            )
            if result is not None:
                return result
        chunks = split_html(render_markdown(text))
        result = await self.edit_html(chat_id, message_id, chunks[0] if chunks else "…")
        for chunk in [] if preview else chunks[1:]:
            await self.send_html(chat_id, chunk, silent=True, thread_id=thread_id)
        return result

    async def send_draft(
        self, chat_id: int, draft_id: int, text: str, *, thread_id=None
    ) -> bool:
        """Ephemeral plain DM preview; final formatting is independent of drafts."""
        if chat_id <= 0 or self._draft_disabled:
            return False
        payload = {
            "chat_id": chat_id,
            "draft_id": draft_id,
            # Bound UTF-16 units without splitting an emoji's surrogate pair.
            "text": text.encode("utf-16-le")[:8192].decode("utf-16-le", errors="ignore"),
        }
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        try:
            result = await self._request("sendMessageDraft", payload, retry_flood=False)
        except TelegramAPIError as exc:
            if exc.code not in {400, 404, 501}:
                raise
            # A topic-specific rejection must not disable drafts in other DMs.
            if exc.code in {404, 501}:
                self._draft_disabled = True
            return False
        return result is True

    async def _formatted_request(self, method: str, payload: dict[str, Any]) -> Any:
        from app.channels.telegram_format import plain_text

        try:
            return await self._request(method, payload)
        except TelegramAPIError as exc:
            if exc.code != 400 or "parse entities" not in exc.description.lower():
                raise
            fallback = dict(payload)
            fallback.pop("parse_mode", None)
            fallback["text"] = plain_text(str(payload["text"]))
            return await self._request(method, fallback)

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        formatted: bool = False,
        reply_markup: dict | None = None,
        silent: bool = False,
        reply_to: int | None = None,
        thread_id: int | None = None,
    ) -> dict[str, Any]:
        from app.channels.telegram_format import render_markdown, split_html
        import html

        chunks = split_html(
            render_markdown(text) if formatted else html.escape(text, quote=False)
        )
        result: dict[str, Any] = {}
        for index, chunk in enumerate(chunks):
            result = await self.send_html(
                chat_id,
                chunk,
                silent=silent or index > 0,
                thread_id=thread_id,
                reply_to=reply_to if index == 0 else None,
                reply_markup=reply_markup if index == len(chunks) - 1 else None,
            )
        return result

    async def edit_message(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        formatted: bool = False,
        reply_markup: dict | None = None,
    ) -> dict:
        from app.channels.telegram_format import render_markdown, split_html
        import html

        chunks = split_html(
            render_markdown(text) if formatted else html.escape(text, quote=False)
        )
        return await self.edit_html(
            chat_id,
            message_id,
            chunks[0] if chunks else "…",
            reply_markup=reply_markup,
        )

    async def send_html(
        self,
        chat_id: int | str,
        rendered: str,
        *,
        reply_markup: dict | None = None,
        silent: bool = False,
        thread_id: int | None = None,
        reply_to: int | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": rendered,
            "parse_mode": "HTML",
            "disable_notification": silent,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        if reply_to is not None:
            payload["reply_parameters"] = {
                "message_id": reply_to,
                "allow_sending_without_reply": True,
            }
        return await self._formatted_request("sendMessage", payload) or {}

    async def edit_html(
        self,
        chat_id: int | str,
        message_id: int,
        rendered: str,
        *,
        reply_markup: dict | None = None,
    ) -> dict:
        try:
            return (
                await self._formatted_request(
                    "editMessageText",
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": rendered,
                        "parse_mode": "HTML",
                        "reply_markup": reply_markup or {"inline_keyboard": []},
                        "link_preview_options": {"is_disabled": True},
                    },
                )
                or {}
            )
        except TelegramAPIError as exc:
            if exc.code == 400 and "message is not modified" in exc.description.lower():
                return {}
            raise

    async def delete_message(self, chat_id: int | str, message_id: int) -> None:
        await self._request(
            "deleteMessage", {"chat_id": chat_id, "message_id": message_id}
        )

    async def remove_keyboard(self, chat_id: int | str, message_id: int) -> None:
        await self._request(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": []},
            },
        )

    async def answer_callback(
        self, callback_id: str, text: str = "", *, alert: bool = False
    ) -> None:
        await self._request(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text[:180],
                "show_alert": alert,
            },
        )

    async def send_typing(
        self, chat_id: int | str, *, thread_id: int | None = None
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        await self._request("sendChatAction", payload, retry_flood=False)

    async def set_commands(self) -> None:
        await self._request(
            "setMyCommands",
            {
                "commands": [
                    {"command": "new", "description": "Start a fresh conversation"},
                    {"command": "stop", "description": "Stop the current task"},
                    {
                        "command": "steer",
                        "description": "Guide the current task without stopping it",
                    },
                    {
                        "command": "queue",
                        "description": "Queue a separate task; list or clear the queue",
                    },
                    {
                        "command": "interrupt",
                        "description": "Replace the current task with a new instruction",
                    },
                    {
                        "command": "mode",
                        "description": "Choose steer, queue, or interrupt for new messages",
                    },
                    {
                        "command": "status",
                        "description": "Show progress and approval mode",
                    },
                    {
                        "command": "compact",
                        "description": "Summarize older messages to free context",
                    },
                    {"command": "manual", "description": "Ask before risky tool calls"},
                    {"command": "smart", "description": "Use smart tool approvals"},
                    {"command": "help", "description": "Show commands and guidance"},
                    {"command": "id", "description": "Show this chat ID"},
                    {"command": "link", "description": "Link to your Tomo account using a code"},
                ]
            },
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def run_channel_turn(
    session_id: str,
    message: str,
    *,
    ui: TelegramTurnUI | None = None,
    attachment_ids: list[str] | None = None,
    recovery: dict[str, Any] | None = None,
) -> str:
    """Run the web turn pipeline; return the latest final (or error) text."""
    # Lazy import: chat → channels.web → channels package must not pull telegram
    # at import time (circular with this module).
    from app.services.chat import start_session_turn

    session = store.get_session(session_id)
    if not session:
        raise ValueError("Session not found")
    history_start = len(store.get_session_history(session_id))
    delivery = recovery.get("delivery") if recovery else None
    if ui is not None and delivery is None:
        delivery = {
            "channel": "telegram", "chat_id": ui.chat_id,
            "actor_id": ui.actor_id, "reply_to": ui.reply_to,
            "thread_id": ui.thread_id,
            "bot": hashlib.sha256(ui.api._token.encode()).hexdigest(),
        }
    from app.channels.telegram_context import bind_turn, reset_turn

    token = bind_turn(ui)
    try:
        # create_task copies this context; the caller does not retain the binding.
        turn, queue = await start_session_turn(
            session_id, message, session["user_id"], attachment_ids=attachment_ids,
            delivery=delivery, recovery=recovery,
        )
    finally:
        reset_turn(token)
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            if ui is not None:
                await ui.consume(chunk)
    finally:
        turn.unsubscribe(queue)
        if turn.task and not turn.task.done():
            turn.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn.task
    history = store.get_session_history(session_id)
    if turn.suspended:
        if ui is not None:
            ui.outcome = "Restarting"
        raise asyncio.CancelledError
    if turn.reply:
        return turn.reply
    for entry in reversed(history[history_start:]):
        kind = entry.get("type")
        if kind in ("final", "error") and entry.get("content"):
            return str(entry["content"])
    return "I couldn't complete that message. Please try again."


def _resolve_agent_id(agent_id: str | None = None) -> str | None:
    if agent_id:
        return agent_id if store.get_agent(agent_id) else None
    coord = store.get_coordinator()
    return coord["id"] if coord else None


def chat_is_allowed(chat_id: int | str) -> bool:
    from app.models.mixins.settings import normalize_telegram_chat_ids

    try:
        allowed = normalize_telegram_chat_ids(
            store.get_settings().get("telegram_allowed_chat_ids", [])
        )
        if str(int(chat_id)) not in allowed:
            return False
        from app.services.telegram_accounts import linked_account

        account = linked_account(chat_id)
        return account is None or bool(account['enabled'])
    except (ValueError, TypeError):
        return False


async def _typing_loop(api: TelegramAPI, chat_id: int | str) -> None:
    while True:
        # Feedback is best effort; a failed typing request must not lose a turn.
        with contextlib.suppress(Exception):
            await api.send_typing(chat_id)
        await asyncio.sleep(4)


async def handle_inbound_text(
    chat_id: int | str,
    text: str,
    *,
    api: TelegramAPI | None = None,
    agent_id: str | None = None,
    send_reply: bool = True,
    ui: TelegramTurnUI | None = None,
    session_id: str | None = None,
    attachment_ids: list[str] | None = None,
    as_content: bool = False,
    chat_type: str | None = None,
    sender_id: int | None = None,
) -> dict[str, Any]:
    """Map chat → session, run one turn, optionally reply on Telegram.

    Returns ``{"session_id", "reply", "agent_id"}``.
    """
    command = (
        text.split()[0].split("@")[0].lower()
        if text.strip() and not attachment_ids and not as_content
        else ""
    )
    if command == "/id":
        reply = f"Your Telegram chat ID: {chat_id}"
        if api is not None and send_reply:
            await api.send_message(chat_id, reply)
        return {"session_id": None, "reply": reply, "agent_id": None}
    if not chat_is_allowed(chat_id):
        reply = (
            f"This chat is not approved. Chat ID: {chat_id}\n"
            "Ask your Tomo administrator to add it in System → Channels → Allowed chat IDs."
        )
        if api is not None and send_reply:
            await api.send_message(chat_id, reply)
        return {"session_id": None, "reply": reply, "agent_id": None, "denied": True}
    if command == "/link":
        from app.services.telegram_accounts import redeem_code

        parts = text.split()
        try:
            if len(parts) != 2:
                raise ValueError('Use /link <code> from Accounts → Link Telegram')
            result = await redeem_code(int(chat_id), parts[1], chat_type=chat_type, sender_id=sender_id)
            account = store.get_user(result['user_id'])
            reply = f"Linked to {account['username']}. Web and Telegram now share your memory."
        except ValueError as exc:
            reply = str(exc)
        if api is not None and send_reply:
            await api.send_message(chat_id, reply)
        return {"session_id": None, "reply": reply, "agent_id": None}
    resolved = _resolve_agent_id(agent_id)
    if not resolved:
        raise ValueError("No agent available for Telegram turns")
    user_id = user_id_for_chat(chat_id)
    if command == "/new":
        from app.runtime.permissions.modes import get_effective_mode, set_session_mode

        previous = store.find_session(resolved, user_id, telegram_chat_id=str(chat_id))
        mode = get_effective_mode(previous)
        session_id = store.create_swarm_session([resolved], user_id=user_id, telegram_chat_id=str(chat_id))
        set_session_mode(session_id, mode)
    elif session_id is None:
        session_id = store.get_or_create_session(resolved, user_id, telegram_chat_id=str(chat_id))
    else:
        session = store.get_session(session_id)
        if (not session or session['user_id'] != user_id
                or session.get('telegram_chat_id') != str(chat_id)):
            raise ValueError('Session does not belong to this Telegram chat')
    logger.info(
        "telegram inbound chat_id=%s session_id=%s agent_id=%s chars=%s",
        chat_id,
        session_id,
        resolved,
        len(text or ""),
    )
    if command == "/new":
        from app.runtime.permissions.modes import mode_payload

        reply = (
            "New conversation started. Send your next message.\nApprovals: "
            + mode_payload(session_id)["label"]
        )
    elif command in {"/start", "/help"}:
        reply = (
            "Welcome to Tomo. Send a message to talk with your coordinator.\n"
            "/new — start a fresh conversation\n/stop — stop the current task\n/status — show progress\n"
            "/compact — summarize older messages to free context\n"
            "/manual, /smart — set approval mode\n/help — show this guide\n/id — show this chat's ID\n"
            "/link <code> — share memory with your web account (DM only)\n"
            "/steer <text> — guide the current task\n/queue <text> — run another task afterwards\n"
            "/queue list, /queue clear — manage waiting tasks\n/interrupt <text> — replace current and waiting tasks\n"
            "/mode steer|queue|interrupt — choose how extra messages behave\n"
            "Use the approval buttons when asked. Reply to questions or tap a choice. "
            "You can send extra guidance during a task. Your conversations also appear in Tomo's Chat page for administrators."
        )
    elif command == "/stop":
        from app.services.chat import cancel_session_turn

        reply = (
            "Stopping the current task…"
            if cancel_session_turn(session_id)
            else "No task is running."
        )
    elif command == "/status":
        from app.runtime.permissions.modes import mode_payload

        mode = mode_payload(session_id)
        reply = f"{'Working' if store.is_session_turn_active(session_id) else 'Ready'} · Approvals: {mode['label']}"
    elif command == "/compact":
        from app.services.compact import compact_session

        if store.is_session_turn_active(session_id):
            reply = "Still working on the current task — run /compact after it finishes."
        else:
            try:
                reply = (await compact_session(session_id, agent_id=resolved))["message"]
            except Exception as exc:
                logger.warning("telegram /compact failed session=%s: %s", session_id, exc)
                reply = f"Compact failed: {exc}"
    elif command in {"/manual", "/smart", "/auto"}:
        from app.runtime.permissions.slash import handle_approval_slash

        reply = handle_approval_slash(command, session_id) or "Approval mode unchanged."
    elif command.startswith("/"):
        reply = "Unknown command. Use /help to see available commands."
    else:
        typing = (
            asyncio.create_task(_typing_loop(api, chat_id))
            if api and send_reply and ui is None
            else None
        )
        try:
            reply = (
                await run_channel_turn(
                    session_id, text, ui=ui, attachment_ids=attachment_ids
                )
                if ui is not None
                else await run_channel_turn(
                    session_id, text, attachment_ids=attachment_ids
                )
                if attachment_ids
                else await run_channel_turn(session_id, text)
            )
        finally:
            if typing:
                typing.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await typing
    if send_reply and reply and api is not None:
        if ui is not None:
            await ui.finish(reply)
        else:
            await api.send_answer(chat_id, reply)
        from app.services.turn_recovery import acknowledge_delivery

        acknowledge_delivery(session_id)
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
    from app.channels.telegram_media import media_descriptor

    if (
        isinstance(update.get("message"), dict)
        and media_descriptor(update["message"]) is not None
    ):
        if api is None:
            raise ValueError("Telegram API is required to receive media")
        msg = update["message"]
        chat_id = int(msg["chat"]["id"])
        if not chat_is_allowed(chat_id):
            return await handle_inbound_text(
                chat_id, "", api=api, agent_id=agent_id, send_reply=send_reply
            )
        dispatcher = TelegramDispatcher(api, agent_id=agent_id)
        return await dispatcher._run(
            chat_id, str(msg.get("caption") or ""), msg, send_reply=send_reply
        )
    if not extracted:
        return None
    chat_id, text = extracted
    return await handle_inbound_text(
        chat_id,
        text,
        api=api,
        agent_id=agent_id,
        send_reply=send_reply,
        chat_type=((update.get('message') or {}).get('chat') or {}).get('type'),
        sender_id=((update.get('message') or {}).get('from') or {}).get('id'),
        as_content='message' not in update,
    )


class TelegramDispatcher:
    """Keep receiving controls while bounded per-chat turns run in background."""

    MAX_ACTIVE_CHATS = 16

    def __init__(self, api: TelegramAPI, *, agent_id: str | None = None) -> None:
        self.api = api
        self.agent_id = agent_id
        self.tasks: dict[int, asyncio.Task] = {}
        self.uis: dict[int, TelegramTurnUI] = {}
        self.actors: dict[int, tuple[int | None, int | None]] = {}
        self.albums: dict[int, list[dict]] = {}
        self.pending: dict[int, list[dict]] = {}
        self.steers: dict[int, dict[str, dict]] = {}
        self.modes: dict[int, str] = {}
        self.stopped: set[int] = set()
        self.replacements: dict[int, dict] = {}
        self.closing = False
        self.running: set[int] = set()

    MAX_PENDING = 10

    def recover_pending(self) -> None:
        """Restore chat slots before polling, keeping sender/topic controls."""
        from app.services import chat
        from app.services.turn_recovery import finish_request, pending_requests

        fingerprint = hashlib.sha256(self.api._token.encode()).hexdigest()
        for request in pending_requests():
            delivery = request.get("delivery") or {}
            if delivery.get("channel") != "telegram" or delivery.get("bot") != fingerprint:
                continue
            chat_id = delivery["chat_id"]
            session = store.get_session(request["session_id"])
            if (not chat_is_allowed(chat_id) or not session
                    or session["user_id"] != user_id_for_chat(chat_id)
                    or session.get('telegram_chat_id') != str(chat_id)):
                finish_request(request)
                continue
            if chat_id in self.tasks or len(self.tasks) >= self.MAX_ACTIVE_CHATS:
                continue
            self.actors[chat_id] = (delivery.get("actor_id"), delivery.get("thread_id"))
            message = {
                "from": {"id": delivery.get("actor_id")},
                "message_id": delivery.get("reply_to"),
                "message_thread_id": delivery.get("thread_id"),
                "_tomo_recovery": request,
            }
            self.tasks[chat_id] = asyncio.create_task(self._drive(chat_id, {
                "text": request["message"], "message": message,
            }))

            def discard_cancelled(task, request=request):
                # Stop can arrive before _drive creates its UI/turn registry.
                if task.cancelled() and not self.closing and not chat._shutting_down:
                    finish_request(request)

            self.tasks[chat_id].add_done_callback(discard_cancelled)

    async def _feedback(self, chat_id: int, item: dict, text: str) -> None:
        if item.get("feedback_id"):
            with contextlib.suppress(Exception):
                await self.api.edit_message(chat_id, item["feedback_id"], text)

    async def _receipt_state(self, chat_id: int, item: dict) -> None:
        if item.get("cancelled"):
            text = "Cancelled. This instruction will not run."
        elif item.get("consumed"):
            text = "✓ Agent read your guidance and will use it in the next model round."
        elif item.get("finished"):
            text = f"{item['outcome']}. See the conversation for the response."
        elif item.get("started"):
            text = "▶ Starting this task."
        else:
            return
        await self._feedback(chat_id, item, text)

    async def _discard_pending(self, chat_id: int) -> None:
        items = self.pending.pop(chat_id, []) + list(
            self.steers.pop(chat_id, {}).values()
        )
        for item in items:
            if not item.get("consumed"):
                item["cancelled"] = True
                await self._feedback(
                    chat_id, item, "Cancelled. This instruction will not run."
                )

    async def _enqueue(
        self, chat_id: int, text: str, message: dict, *, reason: str = "Queued"
    ) -> None:
        items = self.pending.setdefault(chat_id, [])
        album_id = message.get("media_group_id")
        if (
            album_id
            and items
            and album_id == items[-1]["message"].get("media_group_id")
        ):
            messages = items[-1].setdefault("messages", [items[-1]["message"]])
            if len(messages) < 10 and all(
                m.get("message_id") != message.get("message_id") for m in messages
            ):
                messages.append(message)
            return
        if len(items) >= self.MAX_PENDING:
            await self.api.send_message(
                chat_id,
                "The queue is full (10 tasks). Use /queue list or /queue clear.",
                thread_id=message.get("message_thread_id"),
            )
            return
        item = {"text": text, "message": {**message, "_tomo_content": True}}
        items.append(item)
        ui = self.uis.get(chat_id)
        if ui:
            ui.queue_depth = len(items)
        sent = await self.api.send_message(
            chat_id,
            f"↳ {reason} · position {len(items)}. Runs after the current task. Use /queue list or /queue clear.",
            silent=True,
            thread_id=message.get("message_thread_id"),
        )
        item["feedback_id"] = sent.get("message_id")
        # The runner can start while the acknowledgement request is in flight.
        await self._receipt_state(chat_id, item)

    async def _compact_chat(self, chat_id: int, thread_id: int | None = None) -> None:
        """/compact — fold the chat's older messages into a summary marker."""
        resolved = _resolve_agent_id(self.agent_id)
        if not resolved:
            await self.api.send_message(
                chat_id,
                "No agent is available to compact this conversation.",
                thread_id=thread_id,
            )
            return
        session_id = store.find_session(resolved, user_id_for_chat(chat_id), telegram_chat_id=str(chat_id))
        if not session_id:
            await self.api.send_message(
                chat_id,
                "Nothing to compact yet — start chatting first.",
                thread_id=thread_id,
            )
            return
        progress = await self.api.send_message(
            chat_id, "Compacting this conversation…", thread_id=thread_id
        )
        progress_id = (progress or {}).get("message_id")
        from app.services.compact import compact_session

        try:
            reply = (await compact_session(session_id, agent_id=resolved))["message"]
        except Exception as exc:
            logger.warning("telegram /compact failed session=%s: %s", session_id, exc)
            reply = f"Compact failed: {exc}"
        if progress_id:
            try:
                await self.api.edit_message(chat_id, progress_id, reply)
                return
            except Exception:
                pass  # fall through to a fresh reply
        await self.api.send_message(chat_id, reply, thread_id=thread_id)

    async def _busy_input(
        self, chat_id: int, text: str, message: dict, mode: str, *, has_media: bool
    ) -> None:
        ui = self.uis.get(chat_id)
        if mode == "interrupt":
            self.replacements[chat_id] = {
                "text": text,
                "message": {**message, "_tomo_content": True},
            }
            self.stopped.add(chat_id)
            if ui:
                ui.request_stop()
            elif chat_id not in self.running:
                previous = self.tasks[chat_id]
                previous.cancel()
                await asyncio.gather(previous, return_exceptions=True)
                if self.tasks.get(chat_id) is previous:
                    # Cancellation before a task's first step skips its finally block.
                    replacement = self.replacements.pop(chat_id)
                    await self._discard_pending(chat_id)
                    self.stopped.discard(chat_id)
                    self.tasks[chat_id] = asyncio.create_task(
                        self._drive(chat_id, replacement)
                    )
            await self.api.send_message(
                chat_id,
                "↳ Interrupting. Replacing the current task and clearing waiting instructions. A tool already dispatched may still finish.",
                thread_id=message.get("message_thread_id"),
            )
            return
        if mode == "steer" and not has_media and ui and not ui.waiting:
            from app.services.chat import push_session_steer

            result = push_session_steer(ui.session_id, text)
            if result.get("accepted"):
                item = {
                    "text": text,
                    "message": {**message, "_tomo_content": True},
                    "consumed": False,
                }
                self.steers.setdefault(chat_id, {})[result["steer_id"]] = item
                ui.steer_receipts = self.steers[chat_id]
                sent = await self.api.send_message(
                    chat_id,
                    "↳ Guidance received. Waiting for the agent's next model round.",
                    silent=True,
                    thread_id=message.get("message_thread_id"),
                )
                item["feedback_id"] = sent.get("message_id")
                await self._receipt_state(chat_id, item)
                return
            if result.get("reason") == "inbox_full":
                await self.api.send_message(
                    chat_id,
                    "Too much unread guidance (20 messages). Use /queue instead.",
                    thread_id=message.get("message_thread_id"),
                )
                return
        reason = (
            "Queued"
            if mode == "queue"
            else "Queued as a separate turn; the current task cannot read this guidance yet"
        )
        await self._enqueue(chat_id, text, message, reason=reason)

    async def _drive(self, chat_id: int, item: dict) -> None:
        """Own the chat slot through all FIFO turns and interrupt cleanup."""
        self.running.add(chat_id)
        try:
            while not self.closing and chat_is_allowed(chat_id):
                item["started"] = True
                await self._feedback(chat_id, item, "▶ Agent started this task.")
                if item["message"].get("media_group_id"):
                    self.albums.setdefault(
                        chat_id, item.get("messages", [item["message"]])
                    )
                try:
                    result = (
                        None
                        if chat_id in self.stopped
                        else await self._run(chat_id, item["text"], item["message"])
                    )
                except asyncio.CancelledError:
                    result = None
                    if self.closing or chat_id not in self.stopped:
                        raise
                if chat_id in self.stopped:
                    item["finished"] = True
                    item["outcome"] = "Stopped"
                    await self._feedback(
                        chat_id, item, "Stopped. This task was interrupted."
                    )
                    await self._discard_pending(chat_id)
                    self.stopped.discard(chat_id)
                    replacement = self.replacements.pop(chat_id, None)
                    if replacement is None:
                        break
                    item = replacement
                    continue
                item["finished"] = True
                item["outcome"] = (
                    "Completed"
                    if result and result.get("outcome") == "Done"
                    else "Failed"
                )
                await self._feedback(
                    chat_id,
                    item,
                    f"{'✓' if item['outcome'] == 'Completed' else '✗'} {item['outcome']}. See the conversation for the response.",
                )
                # A late accepted steer can miss the final drain. Keep it as a follow-up.
                for receipt in self.steers.pop(chat_id, {}).values():
                    if not receipt.get("consumed"):
                        self.pending.setdefault(chat_id, []).append(receipt)
                        await self._feedback(
                            chat_id,
                            receipt,
                            "↳ The task finished before reading this guidance. Queued as a follow-up.",
                        )
                waiting = self.pending.get(chat_id, [])
                if not waiting:
                    break
                item = waiting.pop(0)
                self.actors[chat_id] = (
                    (item["message"].get("from") or {}).get("id"),
                    item["message"].get("message_thread_id"),
                )
        finally:
            if not item.get("finished") and not item.get("consumed"):
                item["cancelled"] = True
                await self._receipt_state(chat_id, item)
            await self._discard_pending(chat_id)
            self.tasks.pop(chat_id, None)
            self.actors.pop(chat_id, None)
            self.replacements.pop(chat_id, None)
            self.stopped.discard(chat_id)
            self.running.discard(chat_id)

    async def dispatch(self, update: dict) -> None:
        query = update.get("callback_query")
        if isinstance(query, dict):
            chat_id = ((query.get("message") or {}).get("chat") or {}).get("id")
            ui = self.uis.get(chat_id)
            handled = await ui.callback(query) if ui else False
            if not handled:
                await self.api.answer_callback(
                    str(query.get("id") or ""),
                    "This control expired. Send /status or start a new task.",
                )
            return
        # Edited messages must not repeat tool side effects.
        message = update.get("message")
        if not isinstance(message, dict):
            return
        from app.channels.telegram_media import media_descriptor

        has_media = media_descriptor(message) is not None
        extracted = extract_text_message(update)
        if not extracted and has_media:
            try:
                extracted = (
                    int(message["chat"]["id"]),
                    str(message.get("caption") or "").strip(),
                )
            except (KeyError, TypeError, ValueError):
                return
        if not extracted:
            return
        chat_id, text = extracted
        if not has_media and "text" not in message:
            message = {**message, "text": text}
        command = text.split()[0].split("@")[0].lower() if text else ""
        # Captions and transcribed speech are content, never bot commands.
        if has_media:
            command = ""
        if command == "/id" or not chat_is_allowed(chat_id):
            await handle_inbound_text(
                chat_id, text, api=self.api, agent_id=self.agent_id
            )
            return
        ui = self.uis.get(chat_id)
        task = self.tasks.get(chat_id)
        if task and task.done():
            self.tasks.pop(chat_id, None)
            self.uis.pop(chat_id, None)
            self.actors.pop(chat_id, None)
            ui = None
        sender_id = (message.get("from") or {}).get("id")
        thread_id = message.get("message_thread_id")
        busy = task is not None and not task.done()
        if busy and chat_id in self.stopped and command not in {"/status", "/stop"}:
            await self.api.send_message(
                chat_id,
                "The task is stopping. Please send your next instruction after cleanup finishes.",
                thread_id=thread_id,
            )
            return
        if (
            busy
            and (sender_id, thread_id) != self.actors.get(chat_id)
            and (command in {"/steer", "/queue", "/interrupt", "/mode"} or not command)
        ):
            await self.api.send_message(
                chat_id,
                "Only the person who started this task can steer, queue, or interrupt it in this topic.",
                thread_id=thread_id,
            )
            return
        if command == "/link":
            if busy:
                await self.api.send_message(chat_id, 'Finish or stop the current task before linking.')
                return
            result = await handle_inbound_text(
                chat_id, text, agent_id=self.agent_id, send_reply=False,
                chat_type=(message.get('chat') or {}).get('type'), sender_id=sender_id,
            )
            await self.api.send_message(chat_id, result['reply'], thread_id=thread_id)
            return
        if command == "/compact":
            if busy and (sender_id, thread_id) != self.actors.get(chat_id):
                await self.api.send_message(
                    chat_id,
                    "Only the person who started this task can compact this conversation.",
                    thread_id=thread_id,
                )
                return
            if busy:
                await self.api.send_message(
                    chat_id,
                    "Still working on the current task — run /compact after it finishes.",
                    thread_id=thread_id,
                )
                return
            await self._compact_chat(chat_id, thread_id)
            return
        parts = text.split(maxsplit=1)
        args = parts[1].strip() if len(parts) == 2 else ""
        if command == "/mode":
            if args in {"steer", "queue", "interrupt"}:
                self.modes[chat_id] = args
                if ui:
                    ui.input_mode = args
            elif args:
                await self.api.send_message(
                    chat_id,
                    "Use /mode steer, /mode queue, or /mode interrupt.",
                    thread_id=thread_id,
                )
                return
            await self.api.send_message(
                chat_id,
                f"New messages while busy: {self.modes.get(chat_id, 'steer')}. This setting lasts until the bot restarts. Explicit /steer, /queue, and /interrupt override it.",
                thread_id=thread_id,
            )
            return
        if command == "/queue" and args in {"list", "clear"}:
            waiting = self.pending.get(chat_id, [])
            if args == "clear":
                for item in self.pending.pop(chat_id, []):
                    item["cancelled"] = True
                    await self._feedback(
                        chat_id, item, "Cancelled. Removed from the queue."
                    )
                if ui:
                    ui.queue_depth = 0
                reply = f"Cleared {len(waiting)} waiting task(s). The current task continues."
            else:
                reply = (
                    "No waiting tasks."
                    if not waiting
                    else "Waiting tasks:\n"
                    + "\n".join(
                        f"{i}. {item['text'][:120] or '[Media attachment]'}"
                        for i, item in enumerate(waiting, 1)
                    )
                )
            await self.api.send_message(
                chat_id, reply, silent=True, thread_id=thread_id
            )
            return
        if command in {"/steer", "/queue", "/interrupt"}:
            if not args:
                await self.api.send_message(
                    chat_id, f"Use {command} <instruction>.", thread_id=thread_id
                )
                return
            if busy:
                await self._busy_input(
                    chat_id, args, message, command[1:], has_media=False
                )
                return
            # Idle explicit inputs are ordinary turns; nested slashes are content.
            text, message = args, {**message, "text": args, "_tomo_content": True}
            command = ""
        if command.startswith("/"):
            if (
                task
                and not task.done()
                and command
                in {
                    "/stop",
                    "/new",
                    "/auto",
                    "/smart",
                    "/manual",
                }
            ):
                if (sender_id, thread_id) != self.actors.get(chat_id):
                    await self.api.send_message(
                        chat_id,
                        "Only the person who started this task can change or stop it.",
                        thread_id=thread_id,
                    )
                    return
                if command == "/new":
                    await self.api.send_message(
                        chat_id,
                        "Stop the current task with /stop before starting a new conversation.",
                        thread_id=thread_id,
                    )
                    return
                if command == "/stop":
                    self.stopped.add(chat_id)
                    if ui is not None:
                        ui.request_stop()
                    else:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        self.tasks.pop(chat_id, None)
                        self.actors.pop(chat_id, None)
                        await self._discard_pending(chat_id)
                    await self.api.send_message(
                        chat_id, "Stopping the current task…", thread_id=thread_id
                    )
                    return
            if command == "/status" and ui is not None:
                await self.api.send_message(
                    chat_id, ui.status_text(), silent=True, thread_id=thread_id
                )
            else:
                result = await handle_inbound_text(
                    chat_id, text, api=None, agent_id=self.agent_id, send_reply=False
                )
                await self.api.send_message(
                    chat_id, result["reply"], thread_id=thread_id
                )
            return
        if task and not task.done():
            album = self.albums.get(chat_id)
            if (
                has_media
                and album is not None
                and message.get("media_group_id") == album[0].get("media_group_id")
                and (sender_id, thread_id) == self.actors.get(chat_id)
            ):
                if len(album) < 10 and all(
                    m.get("message_id") != message.get("message_id") for m in album
                ):
                    album.append(message)
                return
            if not has_media and ui is not None and await ui.answer_text(message):
                return
            await self._busy_input(
                chat_id,
                text,
                message,
                self.modes.get(chat_id, "steer"),
                has_media=has_media,
            )
            return
        if len(self.tasks) >= self.MAX_ACTIVE_CHATS:
            await self.api.send_message(
                chat_id,
                "Tomo is handling several chats. Please try again shortly.",
                thread_id=thread_id,
            )
            return
        self.actors[chat_id] = (sender_id, thread_id)
        if has_media and message.get("media_group_id"):
            self.albums[chat_id] = [message]
        self.stopped.discard(chat_id)
        self.tasks[chat_id] = asyncio.create_task(
            self._drive(chat_id, {"text": text, "message": message})
        )

    async def _run(
        self, chat_id: int, text: str, message: dict, *, send_reply: bool = True
    ) -> dict | None:
        from app.channels.telegram_ui import TelegramTurnUI

        ui = None
        try:
            # Re-check after scheduling; revoking an ID must close agent access.
            if not chat_is_allowed(chat_id):
                return
            recovery = message.get("_tomo_recovery")
            recovered_session = store.get_session(recovery["session_id"]) if recovery else None
            resolved = _resolve_agent_id(
                recovered_session["coordinator_id"] if recovered_session else self.agent_id
            )
            if not resolved:
                raise ValueError("No coordinator")
            sid = recovery["session_id"] if recovery else store.get_or_create_session(
                resolved, user_id_for_chat(chat_id), telegram_chat_id=str(chat_id)
            )
            ui = TelegramTurnUI(
                self.api,
                chat_id,
                sid,
                actor_id=(message.get("from") or {}).get("id"),
                reply_to=message.get("message_id"),
                thread_id=message.get("message_thread_id"),
            )
            self.uis[chat_id] = ui
            ui.input_mode = self.modes.get(chat_id, "steer")
            ui.queue_depth = len(self.pending.get(chat_id, []))
            ui.steer_receipts = self.steers.setdefault(chat_id, {})
            ui.on_stop = lambda: self.stopped.add(chat_id)
            ui.on_mode = lambda mode: self.modes.__setitem__(chat_id, mode)
            if recovery:
                ui.phase = "Continuing after restart"
            if send_reply:
                await ui.start()
            if ui.stop_requested:
                await ui.finish("Stopped.")
                return
            if recovery:
                from app.services.turn_recovery import acknowledge_delivery

                reply = await run_channel_turn(
                    sid, text, ui=ui, attachment_ids=recovery.get("attachment_ids"),
                    recovery=recovery,
                )
                if send_reply and chat_is_allowed(chat_id):
                    await ui.finish(reply)
                    acknowledge_delivery(sid)
                return {"session_id": sid, "reply": reply, "agent_id": resolved,
                        "outcome": ui.outcome}
            from app.channels.telegram_media import (
                MediaError,
                ingest_media,
                media_descriptor,
                media_messages,
            )

            attachment_ids: list[str] = []
            if media_descriptor(message) is not None:
                ui.phase = "Receiving media"
                ui.receiving_task = asyncio.current_task()
                if chat_id in self.albums:
                    # A fixed bounded window groups a Telegram album into one turn.
                    await asyncio.sleep(0.8)
                messages = self.albums.pop(chat_id, [message])
                messages = [part for item in messages for part in media_messages(item)][
                    :10
                ]
                texts, notices = [], []
                has_non_audio = False
                for item in messages:
                    if not chat_is_allowed(chat_id) or ui.stop_requested:
                        return
                    try:
                        ids, transcript, notice = await ingest_media(
                            self.api,
                            sid,
                            item,
                            allowed=lambda: (
                                chat_is_allowed(chat_id) and not ui.stop_requested
                            ),
                        )
                        attachment_ids.extend(ids)
                        descriptor = media_descriptor(item)
                        if (
                            descriptor
                            and descriptor[0] not in {"voice", "audio"}
                            and not descriptor[3].startswith("audio/")
                        ):
                            has_non_audio = True
                        caption = str(item.get("caption") or "").strip()
                        if caption:
                            texts.append(caption)
                        if transcript:
                            texts.append("[Voice transcript]\n" + transcript)
                        if notice:
                            notices.append(notice)
                    except MediaError as exc:
                        notices.append(str(exc))
                text = "\n\n".join(texts)
                if notices and send_reply:
                    await self.api.send_message(
                        chat_id,
                        "\n".join(dict.fromkeys(notices)),
                        thread_id=ui.thread_id,
                    )
                if not attachment_ids:
                    ui.outcome = "Failed"
                    if send_reply:
                        await ui.finish(
                            "No attachment was received. Please resend your file."
                        )
                    return
                if not text and notices and not has_non_audio:
                    from app.services.chat import attachment_meta_for_ids

                    store.append_session_history(
                        sid,
                        {
                            "type": "user",
                            "content": "[Audio attachment]",
                            "attachment_ids": attachment_ids,
                            "attachments": attachment_meta_for_ids(attachment_ids),
                        },
                    )
                    if send_reply:
                        await ui.finish(
                            "Your audio is saved in this conversation. Send a text message to continue."
                        )
                    return
                text = text or "Please review the attached media."
                ui.receiving_task = None
                ui.phase = "Thinking"
                if ui.stop_requested or not chat_is_allowed(chat_id):
                    return
            result = await handle_inbound_text(
                chat_id,
                text,
                api=self.api,
                agent_id=resolved,
                ui=ui if send_reply else None,
                session_id=sid,
                attachment_ids=attachment_ids or None,
                send_reply=send_reply,
                as_content=bool(message.get("_tomo_content")),
            )
            return {**result, "outcome": ui.outcome}
        except asyncio.CancelledError:
            if ui:
                restarting = ui.outcome == "Restarting"
                ui.outcome = "Restarting" if restarting else "Stopped"
                if send_reply and chat_is_allowed(chat_id) and not restarting and not self.closing:
                    with contextlib.suppress(Exception):
                        await ui.finish("Stopped.")
            raise
        except Exception:
            logger.error("telegram turn or delivery failed chat_id=%s", chat_id)
            if ui:
                ui.outcome = "Failed"
                with contextlib.suppress(Exception):
                    if ui.finished:
                        await self.api.send_message(
                            chat_id,
                            "I couldn't deliver the entire answer. The full response is saved in Tomo's Chat page.",
                            thread_id=ui.thread_id,
                        )
                    else:
                        await ui.finish(
                            "I couldn't complete that message. Please check the session in Tomo or try again."
                        )
        finally:
            if ui:
                await ui.close()
            self.uis.pop(chat_id, None)
            self.albums.pop(chat_id, None)

    async def close(self) -> None:
        self.closing = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.uis.clear()
        self.actors.clear()
        for chat_id in list(self.pending.keys() | self.steers.keys()):
            await self._discard_pending(chat_id)
        self.albums.clear()
        self.replacements.clear()
        self.stopped.clear()


async def poll_once(
    api: TelegramAPI,
    *,
    offset: int = 0,
    timeout: int = 25,
    agent_id: str | None = None,
    dispatcher: TelegramDispatcher | None = None,
) -> int:
    """Fetch and process one getUpdates batch. Returns next offset."""
    updates = await api.get_updates(offset=offset or None, timeout=timeout)
    next_offset = offset
    for update in updates:
        uid = update.get("update_id")
        if isinstance(uid, int):
            next_offset = max(next_offset, uid + 1)
        try:
            if dispatcher is not None:
                await dispatcher.dispatch(update)
            else:
                await process_update(
                    update, api=api, agent_id=agent_id, send_reply=True
                )
        except Exception:
            logger.error("telegram update failed update_id=%s", uid)
            extracted = extract_text_message(update)
            if extracted and chat_is_allowed(extracted[0]):
                with contextlib.suppress(Exception):
                    await api.send_message(
                        extracted[0],
                        "I couldn't complete that message. Please try again.",
                    )
    return next_offset


_supervisor_task: asyncio.Task[None] | None = None
_supervisor_stop: asyncio.Event | None = None


async def _supervisor_loop(stop: asyncio.Event) -> None:
    """Keep the client and dispatcher alive across polls; controls never wait for a turn."""
    offset = 0
    active_token = ""
    api: TelegramAPI | None = None
    dispatcher: TelegramDispatcher | None = None
    try:
        while not stop.is_set():
            settings = store.get_settings()
            token = str(settings.get("telegram_bot_token") or "").strip()
            enabled = bool(settings.get("telegram_enabled"))
            if not enabled or token != active_token:
                if dispatcher:
                    await dispatcher.close()
                    dispatcher = None
                if api:
                    await api.aclose()
                    api = None
                active_token = ""
                offset = 0
            if not (enabled and token):
                try:
                    await asyncio.wait_for(stop.wait(), timeout=3)
                except asyncio.TimeoutError:
                    pass
                continue
            if api is None:
                api = TelegramAPI(token)
                active_token = token
                dispatcher = TelegramDispatcher(api)
                dispatcher.recover_pending()
                fingerprint = hashlib.sha256(token.encode()).hexdigest()
                cursor = settings.get("telegram_update_cursor") or {}
                if isinstance(cursor, dict) and cursor.get("bot") == fingerprint:
                    saved_offset = cursor.get("offset")
                    if (
                        isinstance(saved_offset, int)
                        and not isinstance(saved_offset, bool)
                        and saved_offset >= 0
                    ):
                        offset = saved_offset
                with contextlib.suppress(Exception):
                    await api.set_commands()
            # Cancel work for chats revoked while a turn or approval was in flight.
            if dispatcher:
                for chat_id, ui in list(dispatcher.uis.items()):
                    if not chat_is_allowed(chat_id):
                        from app.services.chat import cancel_session_turn

                        cancel_session_turn(ui.session_id)
            try:
                next_offset = await poll_once(
                    api, offset=offset, timeout=25, dispatcher=dispatcher
                )
                if next_offset != offset:
                    store.update_settings(
                        {
                            "telegram_update_cursor": {
                                "bot": hashlib.sha256(token.encode()).hexdigest(),
                                "offset": next_offset,
                            }
                        }
                    )
                offset = next_offset
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "telegram poll error; check token and ensure only one poller is running"
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
    finally:
        if dispatcher:
            await dispatcher.close()
        if api:
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
