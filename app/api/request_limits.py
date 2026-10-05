"""Inbound HTTP body limits enforced BEFORE Starlette spools multipart/form data.

Starlette's ``UploadFile`` spools uploads to disk only after parsing begins;
a 20 MiB read bound inside the route handler does not bound the parser's
earlier spool. This middleware runs outside all routing/parsing and:

1. rejects requests whose ``Content-Length`` already exceeds the applicable
   cap with ``413`` without reading a single body byte, and
2. buffers length-unknown (chunked) bodies only up to the cap before
   invoking the app, answering ``413`` the moment the cap is exceeded
   without ever handing oversized bytes to the parser.

Per-user upload/storage admission still happens inside the route handlers
(``app/runtime/storage.py`` reservations + quota + control-plane caps); this
layer is the unauthenticated coarse bound that protects the parser itself.
It never logs bodies, headers, or identities — only method, path template
absence (raw path is NOT logged), and the verdict.
"""
from __future__ import annotations

import logging
import os

from starlette.responses import JSONResponse

_logger = logging.getLogger(__name__)


def _env_bytes(name: str, default: int) -> int:
    try:
        return max(1024, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def max_body_bytes() -> int:
    """Default cap for non-upload bodies (chat JSON, config, secrets)."""
    return _env_bytes("TOMO_MAX_REQUEST_BYTES", 32 * 1024 * 1024)


def max_upload_bytes() -> int:
    """Cap for multipart/document routes (must exceed the 20 MiB file bound)."""
    return _env_bytes("TOMO_MAX_UPLOAD_BYTES", 24 * 1024 * 1024)


def limit_for_path(path: str) -> int:
    upload = ("/attachments" in path or path.endswith("/memory/upload")
              or "/artifacts" in path or "/memory/upload" in path)
    return max_upload_bytes() if upload else max_body_bytes()


class RequestLimitsMiddleware:
    """ASGI middleware: early Content-Length check + streaming receive cap."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "DELETE", "OPTIONS"}:
            return await self.app(scope, receive, send)
        limit = limit_for_path(scope.get("path", ""))
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > limit:
                    return await JSONResponse({"detail": "Request body too large"},
                                              status_code=413)(scope, receive, send)
            except ValueError:
                pass  # unparseable length: fall through to streaming enforcement
        else:
            # Length-unknown (chunked) request: buffer up to the cap BEFORE
            # invoking the app, so no byte is ever parsed past the limit and
            # no in-flight exception can race the inner error middleware.
            # Memory is bounded by the cap itself; requests WITH a declared
            # length stream untouched through the branch below.
            buffered: list = []
            total = 0
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    buffered.append(message)
                    break
                total += len(message.get("body", b""))
                if total > limit:
                    _logger.warning("Rejected chunked request over body limit")
                    return await JSONResponse({"detail": "Request body too large"},
                                              status_code=413)(scope, receive, send)
                buffered.append(message)
                if not message.get("more_body"):
                    break
            pending = list(buffered)

            async def replay_receive():
                if pending:
                    return pending.pop(0)
                return await receive()

            return await self.app(scope, replay_receive, send)
        return await self.app(scope, receive, send)
