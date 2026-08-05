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

# ── Context window extraction ─────────────────────────────────────

# Common field names used by OpenAI-compatible providers (vLLM, LiteLLM,
# Ollama-proxy, OpenRouter extras, etc.) to advertise context length.
_CONTEXT_FIELD_NAMES = (
    "context_window",
    "context_length",
    "max_model_len",
    "max_context_length",
    "max_input_tokens",
    "max_seq_len",
    "n_ctx",
    "num_ctx",
    "context",
)

# Nested dict keys where providers commonly hide model metadata.
_CONTEXT_NEST_KEYS = (
    "model_info",
    "meta",
    "metadata",
    "info",
    "architecture",
)

_MIN_CTX = 1024
_MAX_CTX = 50_000_000


def extract_context_window(obj: dict) -> int | None:
    """Pull a positive context window int from a model dict.

    Checks top-level keys, then recurses into common nested dicts
    (``model_info``, ``meta``, ``metadata``, ``info``, ``architecture``).
    Returns ``None`` when no plausible value is found.
    """
    if not isinstance(obj, dict):
        return None

    def _check(d: dict) -> int | None:
        for key in _CONTEXT_FIELD_NAMES:
            val = d.get(key)
            if isinstance(val, (int, float)) and _MIN_CTX <= val <= _MAX_CTX:
                return int(val)
            if isinstance(val, str):
                try:
                    n = int(val)
                except (ValueError, TypeError):
                    continue
                if _MIN_CTX <= n <= _MAX_CTX:
                    return n
        return None

    # Top-level
    result = _check(obj)
    if result is not None:
        return result

    # Nested
    for nest_key in _CONTEXT_NEST_KEYS:
        nested = obj.get(nest_key)
        if isinstance(nested, dict):
            result = _check(nested)
            if result is not None:
                return result

    return None


def _match_model_context(items: list[Any], model: str) -> int | None:
    """Find the context window for *model* in a /models response list.

    Match order:
      1. exact ``id == model``
      2. ``id`` endswith ``/{model}`` or ``:{model}``
      3. ``model`` startswith ``id`` or ``id`` startswith ``model``
         (longest id wins — items should be pre-sorted by id length desc)
    """
    # Sort longest id first so specific matches beat short prefixes.
    sorted_items: list[tuple[str, dict]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "")
        if mid:
            sorted_items.append((mid, item))
    sorted_items.sort(key=lambda t: len(t[0]), reverse=True)

    for mid, item in sorted_items:
        if mid == model:
            return extract_context_window(item)

    model_suffix = "/" + model
    model_colon = ":" + model
    for mid, item in sorted_items:
        if mid.endswith(model_suffix) or mid.endswith(model_colon):
            return extract_context_window(item)

    for mid, item in sorted_items:
        if model.startswith(mid) or mid.startswith(model):
            return extract_context_window(item)

    return None


class LLMConfigError(RuntimeError):
    """Raised when the OpenAI-compatible client is misconfigured."""


class LLMRequestError(RuntimeError):
    """Raised when the upstream chat/completions call fails."""


def format_llm_error(exc: BaseException) -> str:
    """Build a user-visible LLM failure message (never empty).

    Provider SDKs often yield blank ``str(exc)`` or bury the real reason in
    ``body`` / ``response``. Long generations commonly fail as timeout,
    context overflow, or max-output — surface those clearly.
    """
    if isinstance(exc, LLMRequestError):
        text = str(exc).strip()
        if text and text not in {"LLM request failed:", "LLM request failed: "}:
            return text

    parts: list[str] = []
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status > 0:
        parts.append(f"HTTP {status}")

    # Prefer structured body (OpenAI / OpenRouter / vLLM shape).
    detail = _extract_provider_detail(exc)
    if detail:
        parts.append(detail)
    else:
        msg = (getattr(exc, "message", None) or str(exc) or "").strip()
        # Strip noisy SDK prefixes.
        for prefix in ("Error code: ", "Error: "):
            if msg.startswith(prefix):
                msg = msg[len(prefix) :].strip()
        if msg and msg not in {name, f"{name}()"}:
            parts.append(msg)

    if not parts:
        cause = exc.__cause__ or exc.__context__
        if cause is not None and cause is not exc:
            nested = format_llm_error(cause)
            if nested and not nested.startswith("LLM request failed"):
                parts.append(nested)
            elif str(cause).strip():
                parts.append(str(cause).strip())
        if not parts:
            parts.append(name or "unknown error")

    text = " — ".join(parts)
    hint = _hint_for_llm_error(text, status)
    if hint and hint not in text:
        text = f"{text}. {hint}"
    if not text.lower().startswith("llm "):
        text = f"LLM request failed: {text}"
    # Bound UI payload size.
    if len(text) > 800:
        text = text[:797] + "…"
    return text


def _extract_provider_detail(exc: BaseException) -> str:
    """Pull error.message / code from SDK body or HTTP response text."""
    chunks: list[str] = []

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        if isinstance(err, dict):
            for key in ("message", "msg", "detail", "error"):
                val = err.get(key)
                if isinstance(val, str) and val.strip():
                    chunks.append(val.strip())
                    break
            code = err.get("code") or err.get("type") or body.get("code")
            if code and str(code) not in " ".join(chunks):
                chunks.append(f"code={code}")
        elif isinstance(body.get("message"), str):
            chunks.append(body["message"].strip())
    elif isinstance(body, str) and body.strip():
        chunks.append(body.strip()[:400])

    resp = getattr(exc, "response", None)
    if resp is not None and not chunks:
        try:
            data = resp.json()
            if isinstance(data, dict):
                nested = _extract_provider_detail(
                    type("_E", (), {"body": data, "message": ""})()
                )
                if nested:
                    chunks.append(nested)
        except Exception:
            try:
                raw = getattr(resp, "text", None) or ""
                if isinstance(raw, str) and raw.strip():
                    chunks.append(raw.strip()[:400])
            except Exception:
                pass

    return " — ".join(c for c in chunks if c)


def _hint_for_llm_error(text: str, status: int | None) -> str:
    low = text.lower()
    if any(
        m in low
        for m in (
            "context_length",
            "context window",
            "maximum context",
            "too many tokens",
            "token limit",
            "prompt is too long",
            "max_tokens",
            "max output",
        )
    ):
        return (
            "The prompt or reply exceeded the model context/output limit — "
            "try a shorter request, a new session, or a larger-context model"
        )
    if any(m in low for m in ("timed out", "timeout", "deadline exceeded")):
        return (
            "The model took too long (common on long answers) — "
            "retry, raise llm_timeout_seconds in settings, or ask for a shorter reply"
        )
    if status == 401 or "invalid api key" in low or "unauthorized" in low:
        return "Check the API key under System → Models"
    if status == 429 or "rate limit" in low:
        return "Rate limited — wait a moment and retry"
    if status in {500, 502, 503, 504} or "overloaded" in low:
        return "Provider is temporarily unavailable — retry shortly"
    if "content" in low and "filter" in low:
        return "Blocked by the provider content filter"
    return ""


def default_llm_timeout_seconds() -> float:
    """HTTP timeout for chat completions (long answers need headroom)."""
    try:
        from app.services import store

        raw = store.get_settings().get("llm_timeout_seconds", 300)
        return max(30.0, min(float(raw), 3600.0))
    except Exception:
        return 300.0


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


# ── Usage ─────────────────────────────────────────────────────────


def parse_usage(usage: Any) -> tuple[int, int]:
    """Extract ``(prompt_tokens, completion_tokens)`` from a provider usage object.

    Accepts OpenAI SDK objects or plain dicts. Also recognizes Anthropic-style
    ``input_tokens`` / ``output_tokens`` aliases. Returns ``(0, 0)`` when absent.
    """
    if usage is None:
        return 0, 0

    def _get(key: str, *alts: str) -> int:
        keys = (key, *alts)
        if isinstance(usage, dict):
            for k in keys:
                val = usage.get(k)
                if val is not None:
                    try:
                        return max(0, int(val))
                    except (TypeError, ValueError):
                        continue
            return 0
        for k in keys:
            val = getattr(usage, k, None)
            if val is not None:
                try:
                    return max(0, int(val))
                except (TypeError, ValueError):
                    continue
        return 0

    return (
        _get("prompt_tokens", "input_tokens"),
        _get("completion_tokens", "output_tokens"),
    )


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
        timeout: float | None = None,
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
        # Default 300s — long generations often exceed a 60s HTTP timeout and
        # previously surfaced as a blank "LLM request failed:".
        self._timeout = (
            float(timeout) if timeout is not None else default_llm_timeout_seconds()
        )
        self._transport = transport

        http_client = None
        if transport is not None:
            http_client = httpx.AsyncClient(
                transport=transport, timeout=self._timeout
            )

        self._client = openai.AsyncOpenAI(
            base_url=self._base_url,
            api_key=resolved_key,
            timeout=self._timeout,
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
        except LLMRequestError:
            raise
        except Exception as exc:
            _logger.warning(
                "LLM complete failed model=%s: %s", self._model, format_llm_error(exc)
            )
            raise LLMRequestError(format_llm_error(exc)) from exc

        if not resp.choices:
            _logger.warning(
                "LLM empty choices model=%s base_url=%s response=%s",
                self._model,
                self._base_url,
                getattr(resp, "model_dump", lambda: vars(resp))(),
            )
            raise LLMRequestError(
                "LLM request failed: empty choices[] — provider returned no completion"
            )

        first = resp.choices[0]
        if first is None:
            raise LLMRequestError("LLM request failed: choices[0] was null")
        finish = getattr(first, "finish_reason", None) or ""
        message = getattr(first, "message", None)
        if message is None:
            raise LLMRequestError("LLM request failed: response had no message")
        content = getattr(message, "content", None)
        tool_calls = getattr(message, "tool_calls", None)

        if finish in {"length", "max_tokens"} and not tool_calls:
            # Partial text may still be useful — return it, but log.
            _logger.info(
                "LLM hit output length limit model=%s chars=%d",
                self._model,
                len(content or ""),
            )

        prompt_tok, completion_tok = parse_usage(getattr(resp, "usage", None))
        return LLMResponse(
            content=content,
            tool_calls=_parse_tool_calls(tool_calls or []),
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
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

        Requests ``stream_options.include_usage`` so the final chunk can carry
        token counts (OpenAI + most OpenAI-compatible proxies).
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools

        content_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}
        next_auto_idx = 0
        seen_ids: set[str] = set()
        prompt_tok = 0
        completion_tok = 0

        try:
            stream = await self._client.chat.completions.create(**payload)
            async for chunk in stream:
                # Usage often arrives on a trailing chunk with empty choices.
                u_prompt, u_completion = parse_usage(getattr(chunk, "usage", None))
                if u_prompt or u_completion:
                    prompt_tok, completion_tok = u_prompt, u_completion
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

        except LLMRequestError:
            raise
        except Exception as exc:
            _logger.warning(
                "LLM stream failed model=%s deltas=%d: %s",
                self._model,
                len(content_parts),
                format_llm_error(exc),
            )
            # If we already streamed text, still raise so the UI shows why it stopped.
            raise LLMRequestError(format_llm_error(exc)) from exc

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
        if text is None and not raw_tools:
            raise LLMRequestError(
                "LLM request failed: stream ended with no content and no tool calls "
                "(provider closed the connection early — common on long answers or timeouts)"
            )
        yield {
            "type": "done",
            "response": LLMResponse(
                content=text,
                tool_calls=_parse_tool_calls(raw_tools),
                prompt_tokens=prompt_tok,
                completion_tokens=completion_tok,
            ),
        }

    async def _get_json(self, path: str) -> Any:
        """GET *path* on the same base URL and return parsed JSON.

        Uses a short-lived ``httpx.AsyncClient`` with the stored transport
        (if any) so extra provider fields are preserved — the OpenAI SDK
        ``Model`` type strips them.
        """
        kwargs: dict[str, Any] = {"timeout": min(float(self._timeout), 15.0)}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(base_url=self._base_url, **kwargs) as http:
            r = await http.get(path, headers=headers)
            r.raise_for_status()
            return r.json()

    async def fetch_model_context_window(self) -> int | None:
        """GET ``/models`` and extract context window for ``self._model``.

        Match order for model id:
          1. exact ``id == self._model``
          2. ``id`` endswith ``/{model}`` or ``:{model}``
          3. ``model`` startswith ``id`` or ``id`` startswith ``model``
             (longest id wins)

        Falls back to ``GET /models/{model}`` when the list has no match.

        Returns ``int`` or ``None`` on any failure (network, 404, missing
        field).  Never raises for "not found" — logs at debug/info.
        """
        model = self._model

        # ── Try listing all models ────────────────────────────────
        try:
            payload = await self._get_json("/models")
        except Exception as exc:
            _logger.info("GET /models failed (%s); skipping provider lookup", exc)
            return None

        data: list[Any] = []
        if isinstance(payload, dict):
            data = payload.get("data") or payload.get("models") or []
        elif isinstance(payload, list):
            data = payload

        if data:
            ctx = _match_model_context(data, model)
            if ctx is not None:
                return ctx

        # ── Try single-model endpoint ─────────────────────────────
        try:
            single = await self._get_json(f"/models/{model}")
        except Exception:
            pass
        else:
            if isinstance(single, dict):
                ctx = extract_context_window(single)
                if ctx is not None:
                    return ctx

        _logger.debug("no context window found for %s via /models", model)
        return None

    async def aclose(self) -> None:
        """Close the underlying client and release connections."""
        await self._client.close()


__all__ = [
    "OpenAICompatClient",
    "LLMConfigError",
    "LLMRequestError",
    "format_llm_error",
    "default_llm_timeout_seconds",
    "extract_context_window",
    "parse_usage",
    "_match_model_context",
]
