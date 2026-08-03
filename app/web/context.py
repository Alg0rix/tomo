"""Shared Jinja page context for HTML routes and module pages."""

from __future__ import annotations

import logging

from fastapi import Request

from app.core.config import BRAND
from app.core.deps import session_user_id, session_username

logger = logging.getLogger(__name__)


def page_ctx(request: Request, page: str, **extra):
    module_nav: list = []
    try:
        from app.services import store
        from modules.registry import nav_items

        module_nav = nav_items(store.enabled_module_ids())
    except Exception:
        logger.exception("module_nav failed")
        module_nav = []
    return {
        "page": page,
        "brand": BRAND,
        "current_user_id": session_user_id(request),
        "current_username": session_username(request),
        "module_nav": module_nav,
        **extra,
    }
