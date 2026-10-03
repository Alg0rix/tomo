"""Shared Jinja page context for HTML routes and plugin pages."""

from __future__ import annotations

import logging
import zlib

from fastapi import Request

from app.core.config import BRAND
from app.core.deps import session_user_id, session_username
from app.core.self_update import package_version

logger = logging.getLogger(__name__)

ROOM_TINTS = ("warm", "ok", "info", "earth", "rose")


def room_tint(plugin_id: str) -> str:
    return ROOM_TINTS[zlib.crc32(plugin_id.encode()) % len(ROOM_TINTS)]


def page_ctx(request: Request, page: str, **extra):
    from app.plugins.manager import get_manager
    from app.services import store

    plugin_nav = [
        {
            "id": plugin["id"],
            "icon": plugin.get("icon", "puzzle"),
            "kanji": plugin.get("kanji", ""),
            "tint": room_tint(plugin["id"]),
            "label": plugin["name"],
            "path": plugin["pages"][0]["path"],
            "page": "plugin-" + plugin["id"],
        }
        for plugin in get_manager().list()
        if plugin["running"] and plugin["pages"]
    ]
    user_id = session_user_id(request)
    user = store.get_user(user_id) if user_id != "web" else None
    return {
        "page": page,
        "brand": BRAND,
        "app_version": package_version(),
        "current_user_id": user_id,
        "current_username": session_username(request),
        "current_role": (user or {}).get("role") or "",
        "plugin_nav": plugin_nav,
        **extra,
    }
