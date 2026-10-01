"""Private backend consumers over the existing connector RPC transport.

Never expose these RPC parameters/results to agent events or tool outputs.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx

from app.services import secret_store, store
from app.workplaces.hub import hub


def available(workplace_id: str) -> bool:
    session = hub.get(workplace_id)
    if not session or not session.secret_broker:
        return False
    wp = store.get_workplace(workplace_id)
    return bool(wp and wp.get("kind") == "tunnel")


def call(scope: dict[str, Any], method: str, params: dict, timeout: float = 30) -> dict:
    current = secret_store.capability_scope(scope.get("token", ""))
    wid = scope.get("workplace_id")
    if not current or not wid or current.get("workplace_id") != wid:
        raise ValueError("Tunnel broker access missing or expired")
    if not available(wid):
        raise ValueError("Tunnel offline or connector needs the secret-broker update")
    result = hub.call(
        wid, method, {**params, "expires_at": current["expires_at"]}, timeout=timeout
    )
    if not result.get("ok") or not isinstance(result.get("result"), dict):
        # Connector errors may originate below a private boundary. Never forward
        # arbitrary error text, and never blindly retry an uncertain write/POST.
        raise ValueError(
            "Tunnel operation failed or status uncertain; verify before retrying"
        )
    return result["result"]


class HTTPTransport(httpx.AsyncBaseTransport):
    def __init__(self, scope: dict, timeout: float):
        self.scope = scope
        self.timeout = timeout

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        result = await asyncio.to_thread(
            call,
            self.scope,
            "secret_http",
            {
                "url": str(request.url),
                "method": request.method,
                "headers": dict(request.headers),
                "body_b64": base64.b64encode(await request.aread()).decode(),
                "timeout": self.timeout,
            },
            self.timeout + 5,
        )
        try:
            status = result["status_code"]
            encoded = result["body_b64"]
            if (
                not isinstance(status, int)
                or not 100 <= status <= 599
                or not isinstance(encoded, str)
                or len(encoded) > 3_000_000
            ):
                raise ValueError
            body = base64.b64decode(encoded, validate=True)
            if len(body) > 2_000_000:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValueError("Invalid private HTTP response") from None
        return httpx.Response(status, content=body)


def apply_file(
    scope: dict, data: dict, row: Any, mapping: dict[str, str], fmt: str
) -> dict:
    from app.services.secret_files import _MAX_FILE, _render

    raw = data.get("file")
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or "\x00" in raw:
        raise ValueError("Provide a valid target file path")
    prepared = call(scope, "secret_file_read", {"path": raw})
    try:
        encoded = prepared["content_b64"]
        if not isinstance(encoded, str) or len(encoded) > 1_500_000:
            raise ValueError
        existing = base64.b64decode(encoded, validate=True).decode("utf-8")
        if len(existing.encode()) > _MAX_FILE:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise ValueError(
            "Existing remote file must be UTF-8 text within 1 MB"
        ) from None
    private = secret_store.runtime_values(row)
    try:
        content = _render(
            existing, {key: private[field] for key, field in mapping.items()}, fmt
        ).encode()
    except (UnicodeError, RecursionError):
        raise ValueError("Private file cannot be encoded") from None
    if len(content) > _MAX_FILE:
        raise ValueError("Applied file exceeds the 1 MB application limit")
    result = call(
        scope,
        "secret_file_write",
        {
            "path": raw,
            "digest": prepared.get("digest"),
            "content_b64": base64.b64encode(content).decode(),
        },
    )
    if result.get("ok") is not True or not isinstance(result.get("path"), str):
        raise ValueError("Invalid private file application result")
    return {"ok": True, "file": result["path"], "format": fmt, "keys": list(mapping)}
