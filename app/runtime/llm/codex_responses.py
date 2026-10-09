"""Codex/ChatGPT subscription LLM client using the Responses API.

Talks to ``https://chatgpt.com/backend-api/codex`` (or any Responses-API
endpoint) via HTTPX and a bounded SSE reader instead of
chat/completions — the wire format the ChatGPT-subscription Codex backend
actually accepts an OAuth access token against.

Trimmed port of ``tmp/hermes-agent``'s ``agent/codex_responses_adapter.py``
+ ``agent/codex_runtime.py``: only the message/tool conversion and response
normalization needed for a single backend (Codex) — no cross-issuer
encrypted-reasoning replay, no Harmony tool-call-leak recovery, no xAI
answer salvage (see the design spec's "Out of scope").
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from app.core.observability import observe
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx2

from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.llm.http import provider_ssl_context, session_headers, stream_json, user_agent
from app.runtime.llm.prompt_cache import stable_tools
from app.runtime.llm.codex_oauth import DEFAULT_CODEX_BASE_URL
from app.runtime.llm.openai_compat import (
    LLMConfigError,
    LLMRequestError,
    _parse_arguments,
    default_llm_timeout_seconds,
    format_llm_error,
    llm_http_timeout,
    parse_usage,
    parse_usage_details,
)

_logger = logging.getLogger(__name__)


def _flatten_content(content: Any) -> str:
    """Best-effort plain-text flatten of a chat-message ``content`` field."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _content_parts_to_responses(content: list) -> list[dict[str, Any]] | None:
    """Responses-API content parts for a list-form user message.

    ``image_url`` parts become ``input_image`` items; text parts become
    ``input_text``. Returns ``None`` when no image parts exist so callers
    keep the plain-string path for text-only messages.
    """
    parts: list[dict[str, Any]] = []
    has_image = False
    for part in content:
        if isinstance(part, str):
            if part:
                parts.append({"type": "input_text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"image_url", "input_image"}:
            url = part.get("image_url")
            url = url.get("url") if isinstance(url, dict) else url
            if isinstance(url, str) and url:
                has_image = True
                parts.append({"type": "input_image", "image_url": url})
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            parts.append({"type": "input_text", "text": text})
    return parts if has_image and parts else None


def _messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert tomo's chat-style ``messages`` to ``(instructions, input_items)``.

    The first system message becomes the Responses ``instructions`` string.
    Later system messages stay in ``input`` at their original position so
    dynamic turn context does not invalidate the reusable history prefix.
    Everything else becomes an ``input`` item:
    plain user/assistant text, ``function_call`` for assistant tool calls,
    ``function_call_output`` for tool-role results. User messages carrying
    ``image_url`` parts become multimodal ``input_text``/``input_image``
    content (previously the image was silently dropped by the text flatten).
    """
    instructions_parts: list[str] = []
    items: list[dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "additional_tools":
            items.append({"type": "additional_tools", "role": "developer",
                          "tools": stable_tools(_responses_tools(msg["tools"]))})
            continue
        role = msg.get("role")

        if role == "system":
            text = _flatten_content(msg.get("content"))
            if text.strip():
                if instructions_parts or items:
                    items.append({"role": "system", "content": text})
                else:
                    instructions_parts.append(text)
            continue

        if role == "tool":
            call_id = msg.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id.strip():
                continue
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": _flatten_content(msg.get("content")),
            })
            continue

        if role not in {"user", "assistant"}:
            continue

        if role == "user" and isinstance(msg.get("content"), list):
            parts = _content_parts_to_responses(msg["content"])
            if parts is not None:
                items.append({"role": "user", "content": parts})
                continue

        text = _flatten_content(msg.get("content"))
        tool_calls = msg.get("tool_calls")
        if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
            if text.strip():
                items.append({"role": "assistant", "content": text})
            for idx, tc in enumerate(tool_calls):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if not isinstance(fn, dict):
                    continue
                name = fn.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                arguments = fn.get("arguments", "{}")
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments)
                elif not isinstance(arguments, str):
                    arguments = str(arguments)
                call_id = tc.get("id") or tc.get("call_id") or f"call_{idx}"
                items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments or "{}",
                })
            continue

        items.append({"role": role, "content": text})

    return "\n\n".join(instructions_parts), items


def _responses_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Convert chat-completions tool schemas to Responses function-tool schemas."""
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for item in tools:
        fn = item.get("function", {}) if isinstance(item, dict) else {}
        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        converted.append({
            "type": "function",
            "name": name,
            "description": fn.get("description", "") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted or None


def _extract_message_text(item: Any) -> str:
    content = getattr(item, "content", None)
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        ptype = getattr(part, "type", None)
        if ptype not in {"output_text", "text"}:
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            chunks.append(text)
    return "".join(chunks)


def _tool_call_from_item(item: Any) -> ToolCall | None:
    if getattr(item, "type", None) != "function_call":
        return None
    name = getattr(item, "name", "") or ""
    arguments_raw = getattr(item, "arguments", "{}")
    call_id = getattr(item, "call_id", None) or getattr(item, "id", None) or ""
    return ToolCall(id=call_id, name=name, arguments=_parse_arguments(arguments_raw))


def _extract_reasoning_text(item: Any) -> str:
    """Human-readable reasoning summary from a Responses ``reasoning`` item.

    Requesting ``reasoning.summary: "auto"`` makes the backend emit this
    alongside (not instead of) the opaque ``encrypted_content`` blob we
    don't replay (see the design spec's "Out of scope"). This is the part
    worth surfacing to the user as a "thinking" bubble.
    """
    summary = getattr(item, "summary", None)
    if not isinstance(summary, list):
        return ""
    chunks: list[str] = []
    for part in summary:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            chunks.append(text)
    return "\n\n".join(chunks)


def _reasoning_text_from_items(items: list[Any]) -> str | None:
    chunks = [
        t for t in (_extract_reasoning_text(item) for item in items
                    if getattr(item, "type", None) == "reasoning")
        if t
    ]
    return "\n\n".join(chunks) if chunks else None


class CodexResponsesClient:
    """Async Responses-API client for Codex/ChatGPT-subscription profiles.

    Implements the same duck-typed contract as
    :class:`~app.runtime.llm.openai_compat.OpenAICompatClient`
    (``complete``, ``stream_complete``) but wire-encodes against
    ``client.responses.create`` instead of ``chat.completions.create``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        access_token: str | None = None,
        model: str | None = None,
        *,
        reasoning_effort: str | None = None,
        timeout: float | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        resolved_token = (access_token or "").strip()
        if not resolved_token:
            raise LLMConfigError(
                "Configure an API key or ChatGPT sign-in in System → Models."
            )
        self._base_url = (base_url or DEFAULT_CODEX_BASE_URL).rstrip("/")
        self._model = model or "gpt-5-codex"
        self._reasoning_effort = (reasoning_effort or "").strip() or None
        self._timeout = (
            float(timeout) if timeout is not None else default_llm_timeout_seconds()
        )

        self._http_timeout = llm_http_timeout(self._timeout, self._model, self._reasoning_effort)
        self._client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {resolved_token}"},
            timeout=self._http_timeout,
            transport=transport,
            follow_redirects=True,
            verify=provider_ssl_context() if transport is None else True,
        )

    def _payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        endpoint = urlparse(self._base_url)
        version = re.match(r"gpt-(\d+)(?:\.(\d+))?(?:-|$)", self._model)
        native_loading = (
            version is not None and (int(version[1]), int(version[2] or 0)) >= (5, 4)
            and (endpoint.hostname == "api.openai.com"
                 or (endpoint.hostname == "chatgpt.com" and endpoint.path.startswith("/backend-api/codex")))
        )
        if native_loading:
            from app.runtime.tools.discovery import native_tool_layout

            messages, tools = native_tool_layout(messages, tools)
        instructions, input_items = _messages_to_responses_input(messages)
        payload: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": input_items,
            "store": False,
            # Keep the routing key stable across calls and client recreation;
            # live context and growing history must not change it.
            "prompt_cache_key": hashlib.sha256(
                f"{self._model}\0{instructions}".encode("utf-8")
            ).hexdigest(),
        }
        if endpoint.hostname == "chatgpt.com" and endpoint.path.startswith("/backend-api/codex"):
            # ChatGPT derives cache affinity from this header (Codex client.rs).
            payload["extra_headers"] = {"session-id": payload["prompt_cache_key"]}
        elif opencode := session_headers(self._base_url, self._model, messages):
            payload["extra_headers"] = {**opencode, "User-Agent": user_agent()}
        if limit := getattr(self, "max_output_tokens", None):
            payload["max_output_tokens"] = limit
        responses_tools = stable_tools(_responses_tools(tools))
        if responses_tools:
            payload["tools"] = responses_tools
        if self._reasoning_effort:
            # The Codex backend rejects "minimal" (400) — clamp to "low".
            effort = self._reasoning_effort
            if effort == "minimal" and endpoint.hostname == "chatgpt.com":
                effort = "low"
            payload["reasoning"] = {"effort": effort, "summary": "auto"}
        elif (urlparse(self._base_url).hostname == "api.openai.com"
              and self._model.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))):
            payload["reasoning"] = {"summary": "auto"}
        return payload

    @observe("llm")
    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        """Collect a streamed response for callers needing one final result.

        The Codex subscription backend requires streaming even for background
        operations such as memory extraction and session titles.
        """
        response: LLMResponse | None = None
        async for event in self.stream_complete(messages, tools):
            if event.get("type") == "done":
                response = event["response"]
        if response is None:
            raise LLMRequestError("LLM request failed: stream ended without a completion")
        return response

    @observe("llm")
    async def stream_complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a Responses-API turn; yield text deltas then a final response.

        Never reads ``response.completed.response.output`` for content —
        only ``response.output_text.delta`` (text) and
        ``response.output_item.done`` (tool calls, reasoning summaries, and
        a message-text fallback when no deltas were streamed) are used to
        assemble the result, plus ``response.completed.response.usage`` for
        token counts.
        """
        payload = dict(self._payload(messages, tools))
        payload["stream"] = True

        content_parts: list[str] = []
        output_items: list[Any] = []
        prompt_tok = 0
        completion_tok = 0
        usage_details = parse_usage_details(None)
        completed = False
        # Summary parts stream back to back; without a break "**A.**" and
        # "**B.**" fuse into "**A.****B.**" and the markdown falls apart.
        summary_key: tuple[Any, Any] | None = None

        try:
            headers = payload.pop("extra_headers", None)
            async with stream_json(
                self._client, f"{self._base_url}/responses", payload, headers=headers,
            ) as stream:
                async for event in stream:
                    self._last_chunk_time = asyncio.get_running_loop().time()
                    etype = getattr(event, "type", "") or ""
                    if etype == "response.reasoning_summary_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            key = (getattr(event, "item_id", None), getattr(event, "summary_index", None))
                            if summary_key is not None and key != summary_key:
                                yield {"type": "reasoning_delta", "content": "\n\n"}
                            summary_key = key
                            yield {"type": "reasoning_delta", "content": delta}
                    elif etype == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            content_parts.append(delta)
                            yield {"type": "delta", "content": delta}
                    elif etype == "response.output_item.done":
                        item = getattr(event, "item", None)
                        if item is not None:
                            output_items.append(item)
                    elif etype == "response.completed":
                        completed = True
                        resp_obj = getattr(event, "response", None)
                        usage = getattr(resp_obj, "usage", None) if resp_obj is not None else None
                        if usage is not None:
                            prompt_tok, completion_tok = parse_usage(usage)
                            usage_details = parse_usage_details(usage)
                    elif etype == "error":
                        raise LLMRequestError(
                            f"LLM request failed: {getattr(event, 'message', None) or 'Codex Responses stream error'}"
                        )
                    elif etype == "response.incomplete":
                        resp_obj = getattr(event, "response", None)
                        details = getattr(resp_obj, "incomplete_details", None)
                        reason = getattr(details, "reason", None)
                        raise LLMRequestError(
                            f"LLM request failed: Codex Responses incomplete ({reason or 'unknown reason'})"
                        )
                    elif etype == "response.failed":
                        resp_obj = getattr(event, "response", None)
                        err = getattr(resp_obj, "error", None) if resp_obj is not None else None
                        message = getattr(err, "message", None) if err is not None else None
                        raise LLMRequestError(
                            f"LLM request failed: {message or 'Codex Responses stream failed'}"
                        )
        except LLMRequestError:
            raise
        except Exception as exc:
            _logger.warning(
                "Codex Responses stream failed model=%s deltas=%d: %s",
                self._model, len(content_parts), format_llm_error(exc),
            )
            raise LLMRequestError(format_llm_error(exc)) from exc

        if not completed:
            raise LLMRequestError("LLM request failed: stream ended without a completion")

        tool_calls = [tc for tc in (_tool_call_from_item(it) for it in output_items) if tc is not None]
        text = "".join(content_parts) if content_parts else None
        if text is None and not tool_calls:
            # No deltas streamed (e.g. the whole message arrived in one
            # output_item.done) — fall back to the completed message item.
            for item in output_items:
                if getattr(item, "type", None) == "message":
                    fallback = _extract_message_text(item)
                    if fallback:
                        text = fallback
                        break
        if text is None and not tool_calls:
            raise LLMRequestError(
                "LLM request failed: stream ended with no content and no tool calls"
            )
        yield {
            "type": "done",
            "response": LLMResponse(
                content=text, tool_calls=tool_calls,
                prompt_tokens=prompt_tok, completion_tokens=completion_tok,
                reasoning=_reasoning_text_from_items(output_items),
                **usage_details,
            ),
        }

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = ["CodexResponsesClient"]
