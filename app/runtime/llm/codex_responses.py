"""Codex/ChatGPT subscription LLM client using the Responses API.

Talks to ``https://chatgpt.com/backend-api/codex`` (or any Responses-API
endpoint) via ``openai.AsyncOpenAI().responses.create(...)`` instead of
chat/completions — the wire format the ChatGPT-subscription Codex backend
actually accepts an OAuth access token against.

Trimmed port of ``tmp/hermes-agent``'s ``agent/codex_responses_adapter.py``
+ ``agent/codex_runtime.py``: only the message/tool conversion and response
normalization needed for a single backend (Codex) — no cross-issuer
encrypted-reasoning replay, no Harmony tool-call-leak recovery, no xAI
answer salvage (see the design spec's "Out of scope").
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx
import openai

from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.llm.codex_oauth import DEFAULT_CODEX_BASE_URL
from app.runtime.llm.openai_compat import (
    LLMConfigError,
    LLMRequestError,
    _parse_arguments,
    default_llm_timeout_seconds,
    format_llm_error,
    parse_usage,
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


def _messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert tomo's chat-style ``messages`` to ``(instructions, input_items)``.

    System-role messages become the Responses ``instructions`` string
    (joined, in order). Everything else becomes an ``input`` item:
    plain user/assistant text, ``function_call`` for assistant tool calls,
    ``function_call_output`` for tool-role results.
    """
    instructions_parts: list[str] = []
    items: list[dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")

        if role == "system":
            text = _flatten_content(msg.get("content"))
            if text.strip():
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
    return "\n".join(chunks)


def _reasoning_text_from_items(items: list[Any]) -> str | None:
    chunks = [
        t for t in (_extract_reasoning_text(item) for item in items
                    if getattr(item, "type", None) == "reasoning")
        if t
    ]
    return "\n\n".join(chunks) if chunks else None


def _normalize_response(resp: Any) -> LLMResponse:
    output = getattr(resp, "output", None) or []
    content_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            text = _extract_message_text(item)
            if text:
                content_parts.append(text)
        elif item_type == "function_call":
            tc = _tool_call_from_item(item)
            if tc is not None:
                tool_calls.append(tc)

    text = "\n".join(p for p in content_parts if p).strip() or None
    if text is None and not tool_calls:
        out_text = getattr(resp, "output_text", None)
        if isinstance(out_text, str) and out_text.strip():
            text = out_text.strip()
    if text is None and not tool_calls:
        raise LLMRequestError("LLM request failed: Responses API returned no output")

    prompt_tok, completion_tok = parse_usage(getattr(resp, "usage", None))
    return LLMResponse(
        content=text, tool_calls=tool_calls, prompt_tokens=prompt_tok, completion_tokens=completion_tok,
        reasoning=_reasoning_text_from_items(output),
    )


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
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        resolved_token = (access_token or "").strip()
        if not resolved_token:
            raise LLMConfigError(
                "ChatGPT sign-in required in System → Models (subscription profile has no token)."
            )
        self._base_url = (base_url or DEFAULT_CODEX_BASE_URL).rstrip("/")
        self._model = model or "gpt-5-codex"
        self._reasoning_effort = (reasoning_effort or "").strip() or None
        self._timeout = (
            float(timeout) if timeout is not None else default_llm_timeout_seconds()
        )

        http_client = None
        if transport is not None:
            http_client = httpx.AsyncClient(transport=transport, timeout=self._timeout)

        self._client = openai.AsyncOpenAI(
            base_url=self._base_url,
            api_key=resolved_token,
            timeout=self._timeout,
            max_retries=0 if transport is not None else 2,
            http_client=http_client,
        )

    def _payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        instructions, input_items = _messages_to_responses_input(messages)
        payload: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": input_items,
            "store": False,
        }
        responses_tools = _responses_tools(tools)
        if responses_tools:
            payload["tools"] = responses_tools
        if self._reasoning_effort:
            # The Codex backend rejects "minimal" (400) — clamp to "low".
            effort = "low" if self._reasoning_effort == "minimal" else self._reasoning_effort
            payload["reasoning"] = {"effort": effort, "summary": "auto"}
        return payload

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        payload = self._payload(messages, tools)
        try:
            resp = await self._client.responses.create(**payload)
        except LLMRequestError:
            raise
        except Exception as exc:
            _logger.warning(
                "Codex Responses complete failed model=%s: %s", self._model, format_llm_error(exc)
            )
            raise LLMRequestError(format_llm_error(exc)) from exc
        return _normalize_response(resp)

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

        try:
            stream = await self._client.responses.create(**payload)
            async for event in stream:
                etype = getattr(event, "type", "") or ""
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        content_parts.append(delta)
                        yield {"type": "delta", "content": delta}
                elif etype == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is not None:
                        output_items.append(item)
                elif etype == "response.completed":
                    resp_obj = getattr(event, "response", None)
                    usage = getattr(resp_obj, "usage", None) if resp_obj is not None else None
                    if usage is not None:
                        prompt_tok, completion_tok = parse_usage(usage)
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
            ),
        }

    async def aclose(self) -> None:
        await self._client.close()


__all__ = ["CodexResponsesClient"]
