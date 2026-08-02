"""Kanban HTTP routes (API + HTML pages)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse


def register_api(api_router: Any) -> None:
    """No API endpoints yet."""
    return


def register_pages(web_router: Any) -> None:
    from app.core.deps import require_auth, templates
    from app.web.context import page_ctx

    @web_router.get("/board", response_class=HTMLResponse)
    async def board_page(request: Request, _: None = Depends(require_auth)):
        from app.services import store

        if not store.is_module_enabled("kanban"):
            return RedirectResponse("/modules", status_code=303)
        return templates.TemplateResponse(
            request,
            "kanban/page.html",
            page_ctx(request, "board"),
        )
