"""Shared Jinja page context for HTML routes and plugin pages."""

from __future__ import annotations

import logging

from fastapi import Request

from app.core.config import BRAND
from app.core.deps import session_user_id, session_username
from app.core.self_update import package_version

logger = logging.getLogger(__name__)


def page_ctx(request: Request, page: str, **extra):
    from app.plugins.manager import get_manager

    plugin_nav = [
        {
            "id": "plugins",
            "label": "Plugins",
            "path": "/extensions",
            "page": "plugins",
            "icon": "puzzle",
        }
    ]
    for plugin in get_manager().list():
        if plugin["running"] and plugin["pages"]:
            plugin_nav.append(
                {
                    "id": plugin["id"],
                    "icon": plugin.get("icon", "puzzle"),
                    "label": plugin["name"],
                    "path": plugin["pages"][0]["path"],
                    "page": "plugin-" + plugin["id"],
                }
            )
    return {
        "page": page,
        "brand": BRAND,
        "app_version": package_version(),
        "current_user_id": session_user_id(request),
        "current_username": session_username(request),
        "plugin_nav": plugin_nav,
        **extra,
    }
