"""Token Monitor HTTP routes (API + HTML pages)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from modules.token_monitor import ledger


def register_api(api_router: Any) -> None:
    from app.core.deps import require_auth

    r = APIRouter(prefix="/api", tags=["modules", "token_monitor"])

    @r.get("/usage")
    async def usage_dashboard(_: None = Depends(require_auth)):
        from app.services import store

        if not store.is_module_enabled("token_monitor"):
            raise HTTPException(
                status_code=404, detail="Token Monitor module is disabled"
            )

        def _build(conn):
            data = ledger.dashboard(conn)
            agents = []
            for row in data.get("agents") or []:
                ag = store.get_agent(row["agent_id"])
                agents.append(
                    {
                        **row,
                        "name": (ag or {}).get("name") or row["agent_id"],
                    }
                )
            data["agents"] = agents
            return data

        return store.with_db(_build)

    api_router.include_router(r)


def register_pages(web_router: Any) -> None:
    from app.core.deps import require_auth, templates
    from app.web.context import page_ctx

    @web_router.get("/usage", response_class=HTMLResponse)
    async def usage_page(request: Request, _: None = Depends(require_auth)):
        from app.services import store

        if not store.is_module_enabled("token_monitor"):
            return RedirectResponse("/modules", status_code=303)
        return templates.TemplateResponse(
            request,
            "token_monitor/page.html",
            page_ctx(request, "usage"),
        )
