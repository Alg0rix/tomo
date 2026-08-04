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
from app.core import config
from app.core.config import (
    BRAND,
    COOKIE_HTTPS_ONLY,
    HOST,
    PORT,
    RELOAD,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    STATIC_DIR,
    assert_bind_safety,
)
from app.core.deps import templates
from app.core.home import ensure_tomo_home
from app.web import router as web_router


def _bootstrap_runtime() -> None:
    """Create $TOMO_HOME and seed bootstrap secrets before the app binds.

    Must run before :func:`create_app` so SessionMiddleware signs cookies with
    a non-default secret when install/update left ``$TOMO_HOME/.env`` ready —
    or so first start can generate that file itself.
    """
    try:
        ensure_tomo_home()
    except Exception:
        logging.getLogger(__name__).exception("ensure_tomo_home failed")
    try:
        from app.core.bootstrap import apply_bootstrap_to_config, ensure_bootstrap_secrets

        ensure_bootstrap_secrets()
        apply_bootstrap_to_config()
    except Exception:
        logging.getLogger(__name__).exception("bootstrap secrets failed")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Home + secrets already ensured at import; keep a best-effort refresh.
    try:
        ensure_tomo_home()
    except Exception:
        pass
    try:
        from app.services import store as _store

        _store.sync_skills()
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
        title=BRAND,
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.SESSION_SECRET,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=COOKIE_HTTPS_ONLY,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    try:
        from modules.registry import (
            mount_module_static,
            register_module_pages,
            register_module_routes,
        )

        register_module_pages(web_router)
        register_module_routes(api_router)
        mount_module_static(app)
    except Exception:
        logging.getLogger(__name__).exception("module registration failed")

    app.include_router(web_router)
    app.include_router(api_router)

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception):
        if request.url.path.startswith("/api/") or request.url.path.startswith("/v1/"):
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


_bootstrap_runtime()
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

    assert_bind_safety()
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=RELOAD)


if __name__ == "__main__":
    main()
