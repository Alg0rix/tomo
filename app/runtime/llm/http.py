"""Small HTTP/SSE wire reader shared by the two LLM protocols.

Keep provider JSON open-ended: reasoning and usage fields vary across proxies.
No generated SDK schemas are needed to decode these two endpoints.
"""
from __future__ import annotations

import json
import os
import ssl
from functools import lru_cache
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx

_MAX_EVENT_BYTES = 16 * 1024 * 1024


@lru_cache(maxsize=4)
def _ssl_context(cafile: str | None, capath: str | None) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=cafile, capath=capath)


def provider_ssl_context() -> ssl.SSLContext:
    """Share immutable trust roots across short-lived LLM clients.

    Match HTTPX's certifi default and SSL_CERT_FILE/SSL_CERT_DIR precedence.
    Keep verification and hostname checks enabled, including on redirects.
    """
    import certifi

    if os.environ.get("SSL_CERT_FILE"):
        return _ssl_context(os.environ["SSL_CERT_FILE"], None)
    if os.environ.get("SSL_CERT_DIR"):
        return _ssl_context(None, os.environ["SSL_CERT_DIR"])
    return _ssl_context(certifi.where(), None)


def _decode_event(data: list[str]) -> SimpleNamespace | None:
    raw = "\n".join(data)
    if raw.strip() == "[DONE]":
        return None
    event = json.loads(raw, object_hook=lambda obj: SimpleNamespace(**obj))
    if not isinstance(event, SimpleNamespace):
        raise ValueError("Provider stream event must be a JSON object")
    error = getattr(event, "error", None)
    if error:
        raise RuntimeError(getattr(error, "message", None) or str(error))
    return event


async def _events(response: httpx.Response) -> AsyncIterator[SimpleNamespace]:
    data: list[str] = []
    size = 0
    async for line in response.aiter_lines():
        if not line:
            if data:
                event = _decode_event(data)
                if event is None:
                    return
                yield event
                data = []
                size = 0
        elif line.startswith("data:"):
            value = line[5:].removeprefix(" ")
            size += len(value.encode("utf-8"))
            if size > _MAX_EVENT_BYTES:
                raise ValueError("Provider stream event exceeds 16 MiB")
            data.append(value)
    if data:
        event = _decode_event(data)
        if event is not None:
            yield event


@asynccontextmanager
async def stream_json(
    client: httpx.AsyncClient, endpoint: str, payload: dict[str, Any],
    *, headers: dict[str, str] | None = None,
) -> AsyncIterator[AsyncIterator[SimpleNamespace]]:
    """Close the HTTP response on completion, errors, and cancellation."""
    async with client.stream("POST", endpoint, json=payload, headers=headers) as response:
        if response.is_error:
            await response.aread()
        response.raise_for_status()
        yield _events(response)
