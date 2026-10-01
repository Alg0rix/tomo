"""Image-input orchestration for agent turns.

Decides per turn whether user-attached images ride the request as native
``image_url`` parts (vision-capable main model) or are pre-described into
text by the auxiliary vision profile (text-only models, or an explicit
``vision_profile_id`` pin). Descriptions are cached on disk by content hash
so re-sent history images never pay a second model call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DESCRIBE_CONCURRENCY = 3
_DESCRIBE_TIMEOUT_S = 120.0
_CACHE_MAX_ENTRIES = 500


def _cache_path() -> Path:
    from app.core.config import VAR_DIR

    return VAR_DIR / "cache" / "vision_descriptions.json"


def _load_description_cache() -> dict[str, Any]:
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _store_description(key: str, text: str) -> None:
    try:
        cache = _load_description_cache()
        cache[key] = {"text": text, "ts": time.time()}
        if len(cache) > _CACHE_MAX_ENTRIES:
            oldest = sorted(cache, key=lambda k: cache[k].get("ts", 0))
            for k in oldest[: len(cache) - _CACHE_MAX_ENTRIES]:
                cache.pop(k, None)
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache), encoding="utf-8")
    except OSError as exc:
        logger.debug("vision description cache write failed: %s", exc)


def collect_image_attachments(
    history: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Image attachment rows referenced by ``user`` history entries."""
    if not history:
        return []
    from app.services import store
    from app.services.chat import _looks_image_attachment

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in history:
        if entry.get("type") != "user":
            continue
        ids = entry.get("attachment_ids")
        if not ids and isinstance(entry.get("params"), dict):
            ids = entry["params"].get("attachment_ids")
        for aid in ids or []:
            if aid in seen:
                continue
            seen.add(aid)
            try:
                att = store.get_attachment(aid)
            except Exception:
                continue
            if att and _looks_image_attachment(att):
                out.append(att)
    return out


async def _describe_attachment(
    att: dict[str, Any], agent_id: str | None, sem: asyncio.Semaphore
) -> tuple[str, str]:
    """``(attachment_id, description)`` — cached by content hash."""
    aid = str(att.get("id") or "")
    try:
        from app.runtime.llm.vision import analyze_image_data_url
        from app.runtime.llm.vision_image import (
            attachment_bytes,
            encode_image_data_url,
            guess_image_mime,
        )

        raw = attachment_bytes(att)
        if not raw:
            return aid, ""
        key = hashlib.sha256(raw).hexdigest()
        cached = _load_description_cache().get(key)
        if cached and cached.get("text"):
            return aid, cached["text"]
        data_url, _note = encode_image_data_url(raw, guess_image_mime(att.get("file_path") or "", raw))
        name = att.get("original_name") or att.get("filename") or aid
        question = (
            f"Describe this attached image ({name}) so a text-only model can "
            "answer questions about it. Include all readable text, data, and "
            "notable visual detail."
        )
        async with sem:
            text = await asyncio.wait_for(
                analyze_image_data_url(agent_id, data_url, question),
                timeout=_DESCRIBE_TIMEOUT_S,
            )
        if text:
            _store_description(key, text)
        return aid, text
    except Exception as exc:
        logger.info("vision describe failed for attachment %s: %s", aid, exc)
        return aid, ""


async def prepare_image_inputs(
    history: list[dict[str, Any]] | None,
    agent_id: str | None,
) -> dict[str, Any]:
    """``{"mode": "native"|"text", "descriptions": {attachment_id: text}}``.

    Returns immediately (no model calls, no network) when no image
    attachments appear in history — the mode is irrelevant without them, so
    capability lookups stay off the hot path.
    """
    images = collect_image_attachments(history)
    if not images:
        return {"mode": "text", "descriptions": {}}
    from app.runtime.llm.vision import decide_image_input_mode

    mode = await asyncio.to_thread(decide_image_input_mode, agent_id)
    if mode == "native":
        return {"mode": "native", "descriptions": {}}
    from app.runtime.llm.vision import resolve_vision_profile

    if await asyncio.to_thread(resolve_vision_profile, agent_id) is None:
        return {"mode": "text", "descriptions": {}}
    sem = asyncio.Semaphore(_DESCRIBE_CONCURRENCY)
    results = await asyncio.gather(
        *(_describe_attachment(att, agent_id, sem) for att in images)
    )
    return {"mode": "text", "descriptions": {aid: d for aid, d in results if d}}


__all__ = ["collect_image_attachments", "prepare_image_inputs"]
