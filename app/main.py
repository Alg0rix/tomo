"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.core.allocator import configure_allocator

configure_allocator()

from app.core.logging import configure_logging  # noqa: E402

configure_logging()

from app.api import router as api_router  # noqa: E402
from app.core import config  # noqa: E402
from app.core.config import (  # noqa: E402
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
from app.core.deps import templates  # noqa: E402
from app.core.home import ensure_tomo_home  # noqa: E402
from app.web import router as web_router  # noqa: E402


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


def _register_execution_stoppers() -> Callable[[str], None]:
    """Register real confirmed teardown before admitting web/scheduled work."""
    from app.runtime.access import AccessUnavailable
    from app.runtime.isolation import backend
    from app.runtime.isolation import host, jobs
    from app.runtime.mcp import mcp_manager
    from app.runtime.supervision import stop_session as stop_turns
    from app.services import store
    from app.services.background_jobs import manager as background_manager
    from app.services.terminals import terminal_manager
    from app.workplaces import remote_contract, ssh_contract

    loop = asyncio.get_running_loop()

    def stop_managed_session(session_id: str) -> None:
        # Kill OS/container work before draining tasks awaiting those processes.
        # Foundation sets persistent pending/generation fences BEFORE callback.
        failures = []
        # Attempt every backend even if one cannot confirm cleanup. One failed
        # container runtime must not prevent termination of known host work.
        for stopper in (jobs.stop_session, backend.stop_session, host.stop_session,
                        background_manager.stop_session, terminal_manager.stop_session, stop_turns,
                        remote_contract.stop_session, ssh_contract.stop_session_production):
            try:
                stopper(session_id)
            except Exception as exc:
                failures.append(exc)
        if mcp_manager.connected_server_ids():
            # MCP transports are shared/global; conservative teardown closes all
            # rather than claiming an unscoped transport retained no access.
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None
            if current_loop is loop or loop.is_closed():
                failures.append(AccessUnavailable("External service teardown is pending"))
            else:
                try:
                    asyncio.run_coroutine_threadsafe(mcp_manager.close_all(), loop).result(timeout=15)
                except Exception as exc:
                    failures.append(exc)
        if failures:
            raise AccessUnavailable("Managed execution teardown could not be confirmed") from failures[0]

    store.access.register_execution_stopper(stop_managed_session)
    return stop_managed_session


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Home + secrets already ensured at import; keep a best-effort refresh.
    try:
        ensure_tomo_home()
    except Exception:
        logging.getLogger(__name__).exception("Startup home initialization failed")
    try:
        from app.services import store as _store

        _store.sync_skills()
    except Exception:
        logging.getLogger(__name__).exception("Startup skill sync failed")
    # Background supervisors (event-driven; not generic task queues):
    # * telegram — long-poll inbound messages
    # * scheduler — APScheduler wake engine over SQLite schedules (agent turns)
    # Chat turns, learning distill, portal copies stay request/task-local.
    from app.channels.telegram import start_telegram_supervisor, stop_telegram_supervisor
    from app.scheduler import start_scheduler, stop_scheduler
    from app.services.chat import recover_web_turns, suspend_session_turns
    from app.services import background_continuation

    stop_managed_session = _register_execution_stoppers()
    from app.runtime.isolation import lifecycle

    try:
        # Recover orphaned mounts/processes before resuming any owned work.
        # Blocking runtime capability checks must not hold the event loop.
        await asyncio.to_thread(lifecycle.startup)
    except Exception:
        # Keep bootstrap/Admin control-plane recovery usable. Restricted
        # execution still requires the backend's complete capability check.
        logging.getLogger(__name__).exception("Restricted execution unavailable at startup")
    background_continuation.start()
    await recover_web_turns()
    start_telegram_supervisor()
    start_scheduler()
    logging.getLogger(__name__).info("Runtime started", extra={"event": "started"})
    try:
        yield
    finally:
        from app.plugins.manager import get_manager
        from app.services import store

        # Stop ingress before draining all retained OS/task backends. Cancelling
        # a turn alone cannot kill a subprocess executing in a worker thread.
        await stop_scheduler()
        await stop_telegram_supervisor()
        await background_continuation.stop()
        for session in store.list_sessions():
            try:
                await asyncio.to_thread(stop_managed_session, session["id"])
            except Exception:
                logging.getLogger(__name__).exception("Managed shutdown could not be confirmed")
        await suspend_session_turns()
        try:
            await asyncio.to_thread(lifecycle.shutdown)
        except Exception:
            logging.getLogger(__name__).exception("Sandbox shutdown could not be confirmed")
        try:
            from app.runtime.mcp import mcp_manager

            await mcp_manager.close_all()
        except Exception:
            logging.getLogger(__name__).exception("MCP shutdown failed")
        get_manager().close()
        logging.getLogger(__name__).info("Runtime stopped", extra={"event": "stopped"})


def create_app() -> FastAPI:
    app = FastAPI(
        title=BRAND,
        version="0.3.3",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )

    from app.core.observability import RequestLoggingMiddleware

    app.add_middleware(RequestLoggingMiddleware)
    from app.api.access_policy import AccessPolicyMiddleware

    app.add_middleware(AccessPolicyMiddleware, router=app.router)
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.SESSION_SECRET,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=COOKIE_HTTPS_ONLY,
    )
    # Added last = outermost: oversized bodies are rejected before session
    # parsing, policy checks, or multipart spooling. See request_limits.py.
    from app.api.request_limits import RequestLimitsMiddleware

    app.add_middleware(RequestLimitsMiddleware)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from app.plugins.manager import get_manager
    from app.plugins.routes import router as plugins_router

    plugin_manager = get_manager()
    plugin_manager.start()
    app.include_router(plugins_router)
    app.mount("/plugins", plugin_manager, name="plugins")
    app.include_router(web_router)
    app.include_router(api_router)
    from app.api.access_routes import router as access_router
    from app.runtime.access import AccessChangePending, AccessDenied, AccessUnavailable
    from fastapi.responses import JSONResponse

    app.include_router(access_router)

    @app.exception_handler(AccessDenied)
    async def access_denied(request: Request, exc: AccessDenied):
        return JSONResponse(
            {"detail": str(exc) if isinstance(exc, AccessChangePending) else (
                "Execution access unavailable" if isinstance(exc, AccessUnavailable) else "Access denied")},
            status_code=503 if isinstance(exc, AccessUnavailable) else 403,
        )

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


def _websocket_protocol(*args, **kwargs):
    """Load the connector's WebSocket protocol only on the first upgrade."""
    from uvicorn.protocols.websockets.wsproto_impl import WSProtocol

    return WSProtocol(*args, **kwargs)


def main() -> None:
    import uvicorn

    assert_bind_safety()
    # Passing the import string without reload imports this module a second
    # time under app.main when launched with python -m app.main. Each copy
    # builds a full route tree; serve the existing instance in production.
    uvicorn.run(
        "app.main:app" if RELOAD else app, host=HOST, port=PORT, reload=RELOAD,
        loop="asyncio", http="h11", ws=_websocket_protocol,
    )


if __name__ == "__main__":
    main()
