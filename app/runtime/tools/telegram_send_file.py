"""Upload a session artifact using the current turn's Telegram transport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.channels.telegram_context import current_turn
from app.runtime.artifacts.fs import current_session_id, read_artifact_bytes

MAX_UPLOAD_BYTES = 50_000_000


def run(arguments: dict[str, Any]) -> str:
    return "Error: telegram_send_file requires an active asynchronous Telegram turn."


async def run_async(arguments: dict[str, Any]) -> str:
    from app.channels.telegram import chat_is_allowed

    sid = current_session_id()
    ui = current_turn(sid) if sid else None
    if (
        ui is None
        or ui.finished
        or ui.stop_requested
        or not chat_is_allowed(ui.chat_id)
    ):
        return "Error: telegram_send_file is only available in an active approved Telegram chat."
    if not isinstance(arguments, dict) or set(arguments) - {
        "filename",
        "caption",
        "kind",
    }:
        return "Error: Only filename, caption, and kind are accepted; destination is fixed to this chat."
    filename = arguments.get("filename")
    caption = arguments.get("caption", "")
    kind = arguments.get("kind", "auto")
    if not isinstance(filename, str) or not filename.strip():
        return "Error: filename must be the basename of an artifact in this session."
    if not isinstance(caption, str) or len(caption) > 1024:
        return "Error: caption must be plain text of at most 1024 characters."
    if not isinstance(kind, str) or kind not in {"auto", "photo", "document"}:
        return "Error: kind must be auto, photo, or document."
    try:
        data = await asyncio.to_thread(
            read_artifact_bytes, sid, filename, max_bytes=MAX_UPLOAD_BYTES
        )
        # Access may have been revoked while reading a large artifact.
        if ui.finished or ui.stop_requested or not chat_is_allowed(ui.chat_id):
            return "Error: Telegram delivery was stopped or access was revoked."
        result = await ui.api.send_file(
            ui.chat_id,
            filename,
            data,
            caption=caption,
            kind=kind,
            thread_id=ui.thread_id,
            reply_to=ui.reply_to,
            silent=True,
        )
    except Exception as exc:
        return f"Error: Telegram file was not delivered: {exc}"
    if not isinstance(result, dict) or not result.get("message_id"):
        return "Error: Telegram did not confirm file delivery."
    return json.dumps(
        {
            "sent": True,
            "filename": filename,
            "size": len(data),
            "message_id": result.get("message_id"),
        }
    )
