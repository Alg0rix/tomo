"""OpenAI-compatible LLM client using the official ``openai`` SDK.

Talks to any endpoint exposing ``POST {base}/chat/completions`` (OpenAI,
vLLM, LM Studio, OpenRouter, …). The SDK handles request serialization,
retries, and streaming — we map its typed responses to our
:class:`~app.runtime.llm.base.LLMResponse` / :class:`~app.runtime.llm.base.ToolCall`.

**Parallel tool call streaming.** Each fragment's ``index`` is tracked
per-slot so arguments from different tool calls are never concatenated
into the same buffer. When a provider omits ``index``, we auto-assign
sequential indices based on new ``id``/``name`` arrivals.

**JSON auto-repair.** When the model emits malformed JSON in tool call
arguments (missing quotes, duplicated objects, trailing commas),
:func:`_repair_json` attempts to salvage a valid dict before falling back
to ``{"_raw": ...}``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator

import httpx
import openai

from app.runtime.llm.base import LLMResponse, ToolCall

_logger = logging.getLogger(__name__)


class LLMConfigError(RuntimeError):
    """Raised when the OpenAI-compatible client is misconfigured."""


class LLMRequestError(RuntimeError):
    """Raised when the upstream chat/completions call fails."""


# ── JSON auto-repair ──────────────────────────────────────────────


def _extract_balanced_object(text: str, start: int) -> str | None:
    """Extract a balanced ``{...}`` starting at ``text[start]``.

    Walks the string respecting nested braces and quoted strings so a
    candidate object can be isolated even when the surrounding text is
    garbage.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences (```` ```json … ``` ````) if present."""
    s = raw.strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    lines = lines[1:]  # remove opening fence
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _try_parse(text: str) -> dict | None:
    """Parse ``text`` as JSON; return dict on success, None on failure."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _repair_json(raw: str) -> dict | None:
    """Salvage a valid JSON dict from malformed LLM output.

    Strategies (most reliable first):
      1. Direct ``json.loads``
      2. Strip markdown code fences then parse
      3. Remove trailing commas before ``}`` / ``]``
      4. Double-decode (when ``json.loads`` returns a string that is itself JSON)
      5. Scan for any valid ``{...}`` block at every ``{`` offset
         (handles concatenated duplicates — the most common LLM malformation)
    """
    if not raw or not raw.strip():
        return {}

    # 1. Direct parse
    parsed = _try_parse(raw)
    if parsed is not None:
        return parsed

    # 2. Strip markdown fences
    stripped = _strip_fences(raw)
    if stripped != raw:
        parsed = _try_parse(stripped)
        if parsed is not None:
            return parsed

    # 3. Remove trailing commas
    cleaned = re.sub(r",\s*([}\]])", r"\1", stripped)
    if cleaned != stripped:
        parsed = _try_parse(cleaned)
        if parsed is not None:
            return parsed

    # 4. Double-decode (json.loads returned a string containing JSON)
    try:
        inner = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        inner = None
    if isinstance(inner, str):
        parsed = _try_parse(inner)
        if parsed is not None:
            return parsed
        # Also try repair on the inner string
        inner_cleaned = re.sub(r",\s*([}\]])", r"\1", inner)
        if inner_cleaned != inner:
            parsed = _try_parse(inner_cleaned)
            if parsed is not None:
                return parsed

    # 5. Scan for any valid {...} block
    text = stripped
    for i in range(len(text)):
        if text[i] != "{":
            continue
        candidate = _extract_balanced_object(text, i)
        if candidate is None:
            continue
        parsed = _try_parse(candidate)
        if parsed is not None:
            return parsed
        # Try with trailing commas removed
        cleaned_candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        if cleaned_candidate != candidate:
            parsed = _try_parse(cleaned_candidate)
            if parsed is not None:
                return parsed

    return None


# ── Tool call argument parsing ────────────────────────────────────


def _parse_arguments(args_raw: Any) -> dict[str, Any]:
    """Parse tool call arguments into a dict, with auto-repair.

    - Already a dict → return as-is.
    - String → ``json.loads``, falling back to :func:`_repair_json`.
    - Anything else → empty dict.
    """
    if isinstance(args_raw, dict):
        return args_raw
    if not isinstance(args_raw, str):
        return {}
    if not args_raw.strip():
        return {}
    repaired = _repair_json(args_raw)
    if repaired is not None:
        return repaired
    _logger.warning("could not parse tool call arguments: %r", args_raw[:200])
    return {"_raw": args_raw}


def _parse_tool_calls(raw: list[Any]) -> list[ToolCall]:
    """Map SDK tool_call objects *or* raw dicts to :class:`ToolCall`.

    Accepts both :class:`openai.types.ChatCompletionMessageToolCall` (non-
    streaming) and plain ``dict`` (streaming accumulation) so the same
    repair path applies to both.
    """
    calls: list[ToolCall] = []
    for entry in raw or []:
        if hasattr(entry, "id"):
            # SDK typed object
            call_id = entry.id or ""
            fn = getattr(entry, "function", None)
            name = getattr(fn, "name", "") if fn else ""
            args_raw = getattr(fn, "arguments", "{}") if fn else "{}"
        elif isinstance(entry, dict):
            fn = entry.get("function") or {}
            if not isinstance(fn, dict):
                fn = {}
            call_id = entry.get("id") or ""
            name = fn.get("name") or ""
            args_raw = fn.get("arguments", "{}")
        else:
            continue
        arguments = _parse_arguments(args_raw)
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
    return calls


# ── Client ────────────────────────────────────────────────────────


class OpenAICompatClient:
    """Async OpenAI-compatible chat completions client.

    Wraps :class:`openai.AsyncOpenAI` so streaming, retries, and parallel
    tool call accumulation are handled by the SDK. The ``transport``
    parameter (``httpx.MockTransport``) is preserved for test mocking.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        resolved_key = (api_key or "").strip()
        if not resolved_key:
            raise LLMConfigError(
                "Configure LLM in System → Models (API key required)."
            )
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._api_key = resolved_key
        self._model = model or "gpt-4o-mini"
        self._timeout = timeout

        http_client = None
        if transport is not None:
            http_client = httpx.AsyncClient(transport=transport, timeout=timeout)

        self._client = openai.AsyncOpenAI(
            base_url=self._base_url,
            api_key=resolved_key,
            timeout=timeout,
            max_retries=2,
            http_client=http_client,
        )

    @property
    def endpoint(self) -> str:
        """Full chat completions URL (kept for backward compatibility)."""
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

        try:
            resp = await self._client.chat.completions.create(**payload)
        except openai.APIConnectionError as exc:
            raise LLMRequestError(f"LLM request failed: {exc}") from exc
        except openai.APIStatusError as exc:
            raise LLMRequestError(
                f"LLM returned HTTP {exc.status_code}: {exc.message[:200]}"
            ) from exc

        if not resp.choices:
            raise LLMRequestError("LLM response had no choices")

        first = resp.choices[0]
        if first is None:
            raise LLMRequestError("LLM response choices[0] was null")
        message = getattr(first, "message", None)
        if message is None:
            raise LLMRequestError("LLM response had no message")
        content = getattr(message, "content", None)
        tool_calls = getattr(message, "tool_calls", None)

        return LLMResponse(
            content=content,
            tool_calls=_parse_tool_calls(tool_calls or []),
        )

    async def stream_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream chat completions; yield text deltas then a final response.

        Yields ``{"type": "delta", "content": str}`` for each content piece,
        then ``{"type": "done", "response": LLMResponse}``.

        **Parallel tool call tracking.** Each streaming fragment carries an
        ``index`` identifying which tool call it belongs to. Fragments are
        accumulated per-index so arguments never collide across calls.
        When a provider omits ``index``, we auto-assign sequential indices
        based on new ``id``/``name`` arrivals.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        content_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}
        next_auto_idx = 0
        seen_ids: set[str] = set()

        try:
            stream = await self._client.chat.completions.create(**payload)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                piece = getattr(delta, "content", None)
                if piece:
                    content_parts.append(piece)
                    yield {"type": "delta", "content": piece}

                tc_list = getattr(delta, "tool_calls", None)
                if not tc_list:
                    continue

                for tc in tc_list:
                    # Resolve the index for this fragment.
                    idx = getattr(tc, "index", None)
                    if idx is None:
                        # Provider omitted index — try to match by id.
                        tc_id = getattr(tc, "id", None) or ""
                        if tc_id and tc_id in seen_ids:
                            for k, v in tool_acc.items():
                                if v.get("id") == tc_id:
                                    idx = k
                                    break
                        if idx is None:
                            idx = next_auto_idx
                            next_auto_idx += 1
                    else:
                        idx = int(idx)
                        next_auto_idx = max(next_auto_idx, idx + 1)

                    slot = tool_acc.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""}
                    )
                    tc_id = getattr(tc, "id", None) or ""
                    if tc_id:
                        slot["id"] = tc_id
                        seen_ids.add(tc_id)
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        name = getattr(fn, "name", None)
                        if name:
                            slot["name"] = name
                        args_fragment = getattr(fn, "arguments", None)
                        if args_fragment:
                            slot["arguments"] += args_fragment

        except openai.APIConnectionError as exc:
            raise LLMRequestError(f"LLM request failed: {exc}") from exc
        except openai.APIStatusError as exc:
            raise LLMRequestError(
                f"LLM returned HTTP {exc.status_code}: {exc.message[:200]}"
            ) from exc

        raw_tools = []
        for idx in sorted(tool_acc):
            slot = tool_acc[idx]
            raw_tools.append(
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {
                        "name": slot["name"],
                        "arguments": slot["arguments"] or "{}",
                    },
                }
            )
        text = "".join(content_parts) if content_parts else None
        yield {
            "type": "done",
            "response": LLMResponse(
                content=text,
                tool_calls=_parse_tool_calls(raw_tools),
            ),
        }

    async def aclose(self) -> None:
        """Close the underlying client and release connections."""
        await self._client.close()


__all__ = ["OpenAICompatClient", "LLMConfigError", "LLMRequestError"]
