"""OpenCode's Anthropic Messages and Google streaming endpoints.

The agent keeps its existing OpenAI-shaped history and tool schemas.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx2

from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.llm.http import (
    provider_ssl_context,
    session_headers,
    stream_json,
    user_agent,
)
from app.runtime.llm.prompt_cache import stable_tools
from app.runtime.llm.openai_compat import (
    LLMConfigError,
    LLMRequestError,
    _parse_arguments,
    format_llm_error,
    llm_http_timeout,
)


def _blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    for part in content or []:
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            if url.startswith("data:"):
                media, data = url[5:].split(";base64,", 1)
                source = {"type": "base64", "media_type": media, "data": data}
            else:
                source = {"type": "url", "url": url}
            blocks.append({"type": "image", "source": source})
    return blocks


def _message_payload(
    model: str, messages: list[dict], tools: list[dict] | None
) -> dict:
    history = []
    system = []
    for message in messages:
        role = message.get("role", "user")
        blocks = _blocks(message.get("content"))
        if role in ("system", "developer") and not history:
            system.extend(blocks)
            continue
        if role in ("system", "developer"):
            # Portable native endpoints do not all support mid-conversation
            # system roles. Keep runtime context in place, after cached history,
            # as a clearly attributed user block instead of hoisting it.
            blocks = [{"type": "text", "text": "[Runtime context]\n" + b["text"]}
                      for b in blocks if b["type"] == "text"]
            role = "user"
        if role == "tool":
            blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": blocks,
                }
            ]
            role = "user"
        for call in message.get("tool_calls") or []:
            fn = call["function"]
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call["id"],
                    "name": fn["name"],
                    "input": _parse_arguments(fn.get("arguments")),
                }
            )
        if not blocks:
            continue
        # Anthropic requires alternating roles; parallel tool results share a user turn.
        if history and history[-1]["role"] == role:
            history[-1]["content"].extend(blocks)
        else:
            history.append({"role": role, "content": blocks})
    payload = {"model": model, "max_tokens": 8192, "messages": history, "stream": True}
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters")
                or {"type": "object", "properties": {}},
            }
            for t in stable_tools(tools)
        ]
    return payload


class NativeMessagesClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        protocol: str,
        timeout: float,
        transport: httpx2.AsyncBaseTransport | None = None,
    ):
        if not api_key.strip():
            raise LLMConfigError("Configure an API token in System → Models")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._protocol = protocol
        self._reasoning_effort = None
        self._signatures: dict[str, str] = {}
        self._client = httpx2.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "x-api-key": api_key,
                "x-goog-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "User-Agent": user_agent(),
            },
            timeout=llm_http_timeout(timeout, model),
            transport=transport,
            verify=provider_ssl_context() if transport is None else True,
        )

    @property
    def endpoint(self) -> str:
        return self._base_url

    def _google_payload(self, payload: dict) -> dict:
        contents = []
        names = {}
        for message in payload["messages"]:
            parts = []
            for block in message["content"]:
                kind = block["type"]
                if kind == "text":
                    parts.append({"text": block["text"]})
                elif kind == "image":
                    source = block["source"]
                    if source["type"] == "base64":
                        parts.append(
                            {
                                "inlineData": {
                                    "mimeType": source["media_type"],
                                    "data": source["data"],
                                }
                            }
                        )
                    else:
                        parts.append(
                            {
                                "fileData": {
                                    "mimeType": "image/jpeg",
                                    "fileUri": source["url"],
                                }
                            }
                        )
                elif kind == "tool_use":
                    names[block["id"]] = block["name"]
                    parts.append(
                        {
                            "functionCall": {
                                "name": block["name"],
                                "args": block["input"],
                            },
                            "thoughtSignature": self._signatures.get(
                                block["id"], "skip_thought_signature_validator"
                            ),
                        }
                    )
                elif kind == "tool_result":
                    text = "\n".join(b.get("text", "") for b in block["content"])
                    parts.append(
                        {
                            "functionResponse": {
                                "name": names.get(
                                    block["tool_use_id"], block["tool_use_id"]
                                ),
                                "response": {"result": text},
                            }
                        }
                    )
            contents.append(
                {
                    "role": "model" if message["role"] == "assistant" else "user",
                    "parts": parts,
                }
            )
        result = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": payload["max_tokens"]},
        }
        if payload.get("system"):
            result["systemInstruction"] = {
                "parts": [
                    {"text": b["text"]}
                    for b in payload["system"]
                    if b["type"] == "text"
                ]
            }
        if payload.get("tools"):
            result["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t["name"],
                            "description": t["description"],
                            "parametersJsonSchema": t["input_schema"],
                        }
                        for t in payload["tools"]
                    ]
                }
            ]
        return result

    async def complete(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMResponse:
        async for event in self.stream_complete(messages, tools):
            if event["type"] == "done":
                return event["response"]
        raise LLMRequestError("Provider stream ended without a completion")

    async def stream_complete(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        payload = _message_payload(self._model, messages, tools)
        if self._protocol == "messages":
            # Explicit breakpoints: stable instructions plus the growing trail.
            # Provider minimum lengths/TTL still determine actual cache hits.
            if payload.get("system"):
                payload["system"][-1]["cache_control"] = {"type": "ephemeral"}
            if payload["messages"]:
                payload["messages"][-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
        if limit := getattr(self, "max_output_tokens", None):
            payload["max_tokens"] = limit
        if window := getattr(self, "context_window", None):
            payload["max_tokens"] = min(payload["max_tokens"], max(1, window // 4))
        endpoint = self._base_url + "/messages"
        if self._protocol == "google":
            payload = self._google_payload(payload)
            endpoint = (
                self._base_url + f"/models/{self._model}:streamGenerateContent?alt=sse"
            )
        text, thinking = [], []
        calls: dict[int, dict] = {}
        google_calls = []
        prompt_tokens = completion_tokens = 0
        cached_tokens = None
        completed = False
        try:
            async with stream_json(
                self._client,
                endpoint,
                payload,
                headers=session_headers(self._base_url, self._model, messages),
            ) as stream:
                async for raw in stream:
                    event = json.loads(json.dumps(raw, default=vars))
                    if self._protocol == "google":
                        usage = event.get("usageMetadata") or {}
                        prompt_tokens = usage.get("promptTokenCount", prompt_tokens)
                        completion_tokens = usage.get(
                            "candidatesTokenCount", completion_tokens
                        )
                        cached_tokens = usage.get(
                            "cachedContentTokenCount", cached_tokens
                        )
                        for candidate in event.get("candidates") or []:
                            if candidate.get("finishReason"):
                                if candidate["finishReason"] not in (
                                    "STOP",
                                    "MAX_TOKENS",
                                ):
                                    raise LLMRequestError(
                                        f"Provider stopped: {candidate['finishReason']}"
                                    )
                                completed = True
                            for part in (candidate.get("content") or {}).get(
                                "parts"
                            ) or []:
                                if part.get("functionCall"):
                                    call = part["functionCall"]
                                    cid = "call_" + uuid4().hex
                                    google_calls.append(
                                        ToolCall(
                                            cid, call["name"], call.get("args") or {}
                                        )
                                    )
                                    if part.get("thoughtSignature"):
                                        self._signatures[cid] = part["thoughtSignature"]
                                elif part.get("text"):
                                    delta = part["text"]
                                    is_thinking = part.get("thought", False)
                                    (thinking if is_thinking else text).append(delta)
                                    yield {
                                        "type": "reasoning_delta"
                                        if is_thinking
                                        else "delta",
                                        "content": delta,
                                    }
                        continue
                    kind = event.get("type")
                    if kind == "message_start":
                        usage = event["message"].get("usage") or {}
                        cached_tokens = usage.get("cache_read_input_tokens", 0)
                        prompt_tokens = (
                            usage.get("input_tokens", 0)
                            + cached_tokens
                            + usage.get("cache_creation_input_tokens", 0)
                        )
                    elif kind == "content_block_start":
                        block = event["content_block"]
                        if block["type"] == "tool_use":
                            calls[event["index"]] = {**block, "json": ""}
                        elif block.get("text"):
                            text.append(block["text"])
                            yield {"type": "delta", "content": block["text"]}
                    elif kind == "content_block_delta":
                        delta = event["delta"]
                        if delta["type"] == "input_json_delta":
                            calls[event["index"]]["json"] += delta["partial_json"]
                        elif delta["type"] in ("text_delta", "thinking_delta"):
                            is_thinking = delta["type"] == "thinking_delta"
                            value = delta.get("thinking" if is_thinking else "text", "")
                            (thinking if is_thinking else text).append(value)
                            yield {
                                "type": "reasoning_delta" if is_thinking else "delta",
                                "content": value,
                            }
                    elif kind == "message_delta":
                        completion_tokens = (event.get("usage") or {}).get(
                            "output_tokens", completion_tokens
                        )
                    elif kind == "message_stop":
                        completed = True
            if not completed:
                raise LLMRequestError("Provider stream ended before completion")
        except LLMRequestError:
            raise
        except Exception as exc:
            raise LLMRequestError(format_llm_error(exc)) from exc
        tool_calls = (
            google_calls
            if self._protocol == "google"
            else [
                ToolCall(
                    c["id"],
                    c["name"],
                    _parse_arguments(c["json"]) if c["json"] else c.get("input", {}),
                )
                for c in calls.values()
            ]
        )
        yield {
            "type": "done",
            "response": LLMResponse(
                content="".join(text) or None,
                tool_calls=tool_calls,
                reasoning="".join(thinking) or None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
            ),
        }

    async def aclose(self) -> None:
        await self._client.aclose()
