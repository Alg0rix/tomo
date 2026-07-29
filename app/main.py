"""FastAPI application factory."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

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
    SESSION_SECRET,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    STATIC_DIR,
)
from app.core.deps import templates
from app.core.home import ensure_tomo_home
from app.web import router as web_router


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Ensure $TOMO_HOME exists once on server start (non-fatal). Tests that
    # need a specific home call ensure_tomo_home() directly with a temp root.
    try:
        ensure_tomo_home()
    except Exception:
        pass
    from app.channels.telegram import start_telegram_supervisor, stop_telegram_supervisor
    from app.scheduler import start_scheduler, stop_scheduler

    start_telegram_supervisor()
    start_scheduler()
    try:
        yield
    finally:
        await stop_scheduler()
        await stop_telegram_supervisor()


def create_app() -> FastAPI:
    app = FastAPI(
        title=BRAND, version="0.1.0", docs_url=None, redoc_url=None, lifespan=_lifespan
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
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


def _configure_logging() -> None:
    """Set up console logging for the ``app`` namespace at INFO level."""
    _app_logger = logging.getLogger("app")
    _app_logger.setLevel(logging.INFO)
    if not _app_logger.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        _app_logger.addHandler(_h)
    _app_logger.propagate = False


_configure_logging()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=RELOAD)


if __name__ == "__main__":
    main()
