"""Bounded Telegram attachment ingestion into the existing session file store."""

from __future__ import annotations

import mimetypes
import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx2

from app.core import config
from app.core.paths import ensure_under
from app.services.store import store

if TYPE_CHECKING:
    from app.channels.telegram import TelegramAPI

MAX_MEDIA_BYTES = 20 * 1024 * 1024


class MediaError(ValueError):
    """A safe, user-facing ingestion error."""


def _standard_descriptor(message: dict) -> tuple[str, dict, str, str] | None:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        photo = max(
            photos, key=lambda p: int(p.get("width", 0)) * int(p.get("height", 0))
        )
        return "photo", photo, "photo.jpg", "image/jpeg"
    for kind, name, mime in (
        ("voice", "voice.ogg", "audio/ogg"),
        ("audio", "audio.mp3", "audio/mpeg"),
        ("video", "video.mp4", "video/mp4"),
        ("video_note", "video-note.mp4", "video/mp4"),
        ("animation", "animation.mp4", "video/mp4"),
        ("document", "document.bin", "application/octet-stream"),
        ("sticker", "sticker.webp", "image/webp"),
    ):
        item = message.get(kind)
        if isinstance(item, dict) and item.get("file_id"):
            if kind == "sticker" and item.get("is_video"):
                name, mime = "sticker.webm", "video/webm"
            elif kind == "sticker" and item.get("is_animated"):
                name, mime = "sticker.tgs", "application/gzip"
            name = str(item.get("file_name") or name).replace("\\", "/")
            name = Path(name).name[:120] or "upload.bin"
            mime = str(item.get("mime_type") or mimetypes.guess_type(name)[0] or mime)
            return kind, item, name, mime
    return None


def media_messages(message: dict) -> list[dict]:
    """Expand rich media blocks into the same ingestion path as ordinary files."""
    from app.channels.telegram_format import rich_plain_text

    if _standard_descriptor(message) is not None:
        return [message]
    rich = message.get("rich_message")
    if not isinstance(rich, dict):
        return []
    found: list[dict] = []

    def visit(value, depth=0):
        if depth > 20 or len(found) >= 10:
            return
        if isinstance(value, list):
            for item in value[:200]:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            kind = value.get("type")
            field = "voice" if kind == "voice_note" else kind
            if field in {"photo", "audio", "video", "voice", "animation", "document"}:
                item = {**message, field: value.get(kind)}
                item.pop("rich_message", None)
                if _standard_descriptor(item) is not None:
                    found.append(item)
            for key in ("blocks", "items"):
                if key in value:
                    visit(value[key], depth + 1)

    visit(rich.get("blocks", []))
    if found:
        found[0]["caption"] = rich_plain_text(rich.get("blocks", []))
    return found


def media_descriptor(message: dict) -> tuple[str, dict, str, str] | None:
    items = media_messages(message)
    return _standard_descriptor(items[0]) if items else None


async def transcribe_audio(data: bytes, name: str, mime: str, *, client=None) -> str:
    """Use an explicitly configured OpenAI-compatible transcription service."""
    settings = store.get_settings()
    if not settings.get("telegram_transcription_enabled"):
        raise MediaError(
            "Audio saved. Enable voice transcription in System → Channels, or send a text caption."
        )
    base = str(settings.get("telegram_transcription_base_url") or "").rstrip("/")
    model = str(settings.get("telegram_transcription_model") or "").strip()
    key = str(settings.get("telegram_transcription_api_key") or "").strip()
    if not base or not model:
        raise MediaError(
            "Audio saved. Configure the transcription service URL and model in System → Channels."
        )
    owns_client = client is None
    client = client or httpx2.AsyncClient(timeout=90, follow_redirects=False)
    try:
        response = await client.post(
            base + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"} if key else {},
            data={"model": model, "response_format": "json"},
            files={"file": (name, data, mime)},
        )
        response.raise_for_status()
        text = response.json().get("text")
        if not isinstance(text, str) or not text.strip():
            raise MediaError(
                "Audio saved, but no speech was recognized. Please send a text caption."
            )
        return text.strip()[:32000]
    except (httpx2.HTTPError, ValueError, AttributeError) as exc:
        if isinstance(exc, MediaError):
            raise
        raise MediaError(
            "Audio saved, but transcription failed. Check the service configuration or send a text caption."
        ) from None
    finally:
        if owns_client:
            await client.aclose()


async def ingest_media(
    api: TelegramAPI,
    session_id: str,
    message: dict,
    *,
    allowed: Callable[[], bool] | None = None,
) -> tuple[list[str], str, str]:
    """Return attachment IDs, transcript, and any nonfatal transcription notice."""
    descriptor = media_descriptor(message)
    if descriptor is None:
        return [], "", ""
    kind, item, name, mime = descriptor
    if int(item.get("file_size") or 0) > MAX_MEDIA_BYTES:
        raise MediaError("This file is too large. Send a file smaller than 20 MB.")
    data = await api.download_file(str(item["file_id"]), max_bytes=MAX_MEDIA_BYTES)
    if allowed is not None and not allowed():
        raise asyncio.CancelledError
    attachment_id = "att_" + uuid4().hex[:18]
    root = (Path(config.TOMO_HOME) / "attachments").resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = ensure_under(root, session_id)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix
    # Extension never carries user-controlled path characters into stored paths.
    suffix = (
        suffix
        if suffix.isascii() and suffix[1:].isalnum() and len(suffix) <= 12
        else ".bin"
    )
    filename = attachment_id + suffix
    path = ensure_under(directory, filename)
    try:
        path.write_bytes(data)
        store.create_attachment(
            attachment_id=attachment_id,
            session_id=session_id,
            filename=filename,
            original_name=name,
            mime_type=mime,
            size_bytes=len(data),
            file_path=str(path),
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    transcript, notice = "", ""
    if kind in {"voice", "audio"} or mime.startswith("audio/"):
        try:
            transcript = await transcribe_audio(data, name, mime)
        except MediaError as exc:
            notice = str(exc)
    return [attachment_id], transcript, notice
