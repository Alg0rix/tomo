"""FastAPI application factory."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.core.config import (
    BRAND,
    HOST,
    PORT,
    RELOAD,
    SECRET_KEY,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    STATIC_DIR,
)
from app.core.deps import templates
from app.web import router as web_router


def create_app() -> FastAPI:
    app = FastAPI(title=BRAND, version="0.1.0", docs_url=None, redoc_url=None)

    app.add_middleware(
        SessionMiddleware,
        secret_key=SECRET_KEY,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=False,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(web_router)
    app.include_router(api_router)

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception):
        if request.url.path.startswith("/api/"):
            return HTMLResponse(
                json.dumps({"error": "not_found"}),
                status_code=404,
                media_type="application/json",
            )
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "page": "error",
                "brand": BRAND,
                "code": 404,
                "message": "That page doesn't exist.",
            },
            status_code=404,
        )

    @app.exception_handler(303)
    async def see_other(request: Request, exc: Exception):
        loc = getattr(exc, "headers", {}).get("Location", "/")
        return RedirectResponse(loc, status_code=303)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=RELOAD)


if __name__ == "__main__":
    main()
