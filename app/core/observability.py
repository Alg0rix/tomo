"""Content-free lifecycle tracing at shared runtime boundaries."""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

log_context: ContextVar[dict] = ContextVar("tomo_log_context", default={})
logger = logging.getLogger(__name__)


@contextmanager
def bind_context(**fields):
    token = log_context.set({**log_context.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        log_context.reset(token)


def observe(log_type: str):
    """Trace async functions/streams without logging prompts, arguments or results."""
    def decorate(fn):
        signature = inspect.signature(fn)

        def context(args, kwargs):
            bound = signature.bind_partial(*args, **kwargs).arguments
            fields = {k: bound[k] for k in ("session_id", "agent_id", "coordinator_id", "name") if k in bound}
            if "name" in fields:
                fields["tool_name"] = fields.pop("name")
            client = bound.get("self") or bound.get("client")
            if client is not None and log_type == "llm":
                fields["provider"] = type(client).__name__
                fields["model"] = getattr(client, "_model", None)
            schedule = bound.get("schedule")
            if isinstance(schedule, dict):
                fields["schedule_id"] = schedule.get("id")
            return {**log_context.get(), **fields, "operation_id": uuid.uuid4().hex, "log_type": log_type, "operation": fn.__name__}

        def emit(fields, event, started, level=logging.INFO):
            logger.log(level, "%s %s", fields["operation"], event,
                       extra={**fields, "event": event, "duration_ms": round((time.monotonic() - started) * 1000)})

        if inspect.isasyncgenfunction(fn):
            @wraps(fn)
            async def stream(*args, **kwargs):
                fields = context(args, kwargs)
                started = time.monotonic()
                emit(fields, "started", started)
                iterator = fn(*args, **kwargs)
                outcome = "closed"
                try:
                    while True:
                        # Restore context before yielding to the caller; generators can
                        # be closed from a different task on client disconnect.
                        with bind_context(**fields):
                            try:
                                item = await anext(iterator)
                            except StopAsyncIteration:
                                outcome = "completed"
                                break
                        yield item
                except asyncio.CancelledError:
                    outcome = "cancelled"
                    raise
                except Exception:
                    outcome = "failed"
                    logger.exception("%s failed", fn.__name__, extra={**fields, "event": "failed"})
                    raise
                finally:
                    try:
                        with bind_context(**fields):
                            await iterator.aclose()
                    finally:
                        emit(fields, outcome, started, logging.ERROR if outcome == "failed" else logging.INFO)
            return stream

        @wraps(fn)
        async def call(*args, **kwargs):
            fields = context(args, kwargs)
            started = time.monotonic()
            emit(fields, "started", started)
            with bind_context(**fields):
                try:
                    result = await fn(*args, **kwargs)
                except asyncio.CancelledError:
                    emit(fields, "cancelled", started)
                    raise
                except Exception:
                    logger.exception("%s failed", fn.__name__, extra={**fields, "event": "failed", "duration_ms": round((time.monotonic() - started) * 1000)})
                    raise
            outcome = "completed"
            if isinstance(result, str) and result.startswith("Error:"):
                outcome = "failed"
            elif isinstance(result, dict) and result.get("status") in {"failed", "error", "skipped"}:
                outcome = result["status"]
            emit(fields, outcome, started, logging.ERROR if outcome == "failed" else logging.INFO)
            return result
        return call
    return decorate


class RequestLoggingMiddleware:
    """ASGI middleware: measures full streams, excludes query strings and bodies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.monotonic()
        fields = {"request_id": uuid.uuid4().hex, "log_type": "http", "method": scope["method"]}
        status = None

        async def traced_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", fields["request_id"].encode())]}
            await send(message)

        with bind_context(**fields):
            logger.info("HTTP request started", extra={**fields, "event": "started"})
            try:
                await self.app(scope, receive, traced_send)
            except Exception:
                logger.exception("HTTP request failed", extra={**fields, "event": "failed"})
                raise
            finally:
                route = scope.get("route")
                logger.log(logging.ERROR if status is None or status >= 500 else logging.INFO,
                           "HTTP request finished", extra={**fields, "event": "finished", "status_code": status,
                           "route": getattr(route, "path", None), "duration_ms": round((time.monotonic() - started) * 1000)})
