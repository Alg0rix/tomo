"""OpenAI-compatible HTTP LLM client.

Talks to any endpoint exposing ``POST {base}/chat/completions`` (OpenAI,
vLLM, LM Studio, OpenRouter, …) using :mod:`httpx`. Completion-first: the
whole JSON body is awaited and mapped to an :class:`LLMResponse`.

Tool calls in the OpenAI response (``choices[0].message.tool_calls``) are
parsed into :class:`ToolCall` objects with already-decoded ``arguments``
dicts (the wire format sends arguments as a JSON *string*).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.runtime.llm.base import LLMResponse, ToolCall


class LLMConfigError(RuntimeError):
    """Raised when the OpenAI-compatible client is misconfigured."""


class LLMRequestError(RuntimeError):
    """Raised when the upstream chat/completions call fails."""


def _parse_tool_calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    """Map OpenAI ``tool_calls`` entries to :class:`ToolCall` objects.

    ``ToolCall.arguments`` is always a dict: non-dict entries are skipped,
    and JSON arguments that decode to a non-object (e.g. ``[1, 2]``) are
    wrapped in ``{"_raw": ...}`` so the agent loop can still dispatch.
    """
    calls: list[ToolCall] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        args_raw = fn.get("arguments")
        if isinstance(args_raw, str):
            try:
                parsed = json.loads(args_raw) if args_raw else {}
            except json.JSONDecodeError:
                # Keep the raw payload so callers can debug bad model output.
                parsed = {"_raw": args_raw}
            arguments = parsed if isinstance(parsed, dict) else {"_raw": args_raw}
        elif isinstance(args_raw, dict):
            arguments = args_raw
        else:
            arguments = {}
        calls.append(
            ToolCall(
                id=entry.get("id") or "",
                name=fn.get("name") or "",
                arguments=arguments,
            )
        )
    return calls


class OpenAICompatClient:
    """Async OpenAI-compatible chat completions client."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # Read config lazily so tests/monkeypatching and direct construction
        # both see live values rather than import-time snapshots.
        from app.core import config

        self._base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self._model = model or config.LLM_MODEL
        self._timeout = timeout

        resolved_key = api_key if api_key is not None else config.LLM_API_KEY
        # Strip whitespace so a key of only spaces is treated as missing.
        resolved_key = (resolved_key or "").strip()
        if not resolved_key:
            raise LLMConfigError(
                "TOMO_LLM_API_KEY is required when TOMO_LLM_PROVIDER=openai_compat."
            )
        self._api_key = resolved_key

        # A single reusable client keeps connection pools alive across
        # ``complete()`` calls; tests inject an ``httpx.MockTransport`` here.
        # Call ``aclose()`` to release the underlying connections.
        self._client = httpx.AsyncClient(timeout=self._timeout, transport=transport)

    @property
    def endpoint(self) -> str:
        """Full chat completions URL.

        If ``base_url`` already ends with ``/chat/completions`` it is used
        as-is; otherwise ``/chat/completions`` is appended.
        """
        if self._base_url.endswith("/chat/completions"):
            return self._base_url
        return f"{self._base_url}/chat/completions"

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = await self._client.post(
                self.endpoint, json=payload, headers=headers
            )
        except httpx.RequestError as exc:
            raise LLMRequestError(f"LLM request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMRequestError(
                f"LLM returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMRequestError("LLM returned a non-JSON body") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMRequestError("LLM response had no choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMRequestError("LLM response choices[0] was malformed")
        message = first.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        return LLMResponse(
            content=message.get("content"),
            tool_calls=_parse_tool_calls(message.get("tool_calls") or []),
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client and release connections."""
        await self._client.aclose()


__all__ = ["OpenAICompatClient", "LLMConfigError", "LLMRequestError"]
