"""OpenAI-compatible client HTTP mapping tests via ``httpx.MockTransport``.

No real network calls: a mock transport inspects the outgoing request and
returns canned OpenAI-shaped JSON so we can verify the wire mapping
(content <-> LLMResponse, tool_calls <-> ToolCall, error handling).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.runtime.llm.openai_compat import (
    LLMConfigError,
    LLMRequestError,
    OpenAICompatClient,
    _repair_json,
)

_BASE = "https://example.test/v1"
_KEY = "sk-test"
_MODEL = "gpt-4o-mini"


def _client(transport: httpx.MockTransport, **kw) -> OpenAICompatClient:
    return OpenAICompatClient(
        base_url=_BASE,
        api_key=_KEY,
        model=_MODEL,
        transport=transport,
        **kw,
    )


def _completion_body(*, content: str | None = None, tool_calls=None) -> dict:
    message: dict = {}
    if content is not None:
        message["content"] = content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
    }


def test_missing_api_key_raises() -> None:
    with pytest.raises(LLMConfigError):
        OpenAICompatClient(api_key="", base_url=_BASE, model=_MODEL)


async def test_plain_content_mapped_and_request_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == f"Bearer {_KEY}"
        body = json.loads(request.content)
        assert body["model"] == _MODEL
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert "tools" not in body
        return httpx.Response(200, json=_completion_body(content="hello back"))

    resp = await _client(httpx.MockTransport(handler)).complete(
        [{"role": "user", "content": "hi"}]
    )
    assert resp.content == "hello back"
    assert resp.tool_calls == []


async def test_tools_forwarded_and_tool_calls_mapped() -> None:
    raw_tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "calculator",
                "arguments": '{"expression": "2 + 2"}',
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"] == [
            {"type": "function", "function": {"name": "calculator"}}
        ]
        return httpx.Response(
            200, json=_completion_body(content=None, tool_calls=raw_tool_calls)
        )

    resp = await _client(httpx.MockTransport(handler)).complete(
        [{"role": "user", "content": "calculate 2 + 2"}],
        tools=[{"type": "function", "function": {"name": "calculator"}}],
    )
    assert resp.content is None
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "calculator"
    assert call.arguments == {"expression": "2 + 2"}


async def test_multiple_tool_calls_and_dict_arguments() -> None:
    raw = [
        {
            "id": "c1",
            "type": "function",
            "function": {
                "name": "calculator",
                "arguments": '{"expression": "1+1"}',
            },
        },
        {
            "id": "c2",
            "type": "function",
            "function": {"name": "search", "arguments": {"q": "tomo"}},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_body(tool_calls=raw))

    resp = await _client(httpx.MockTransport(handler)).complete(
        [{"role": "user", "content": "do two things"}]
    )
    assert [c.name for c in resp.tool_calls] == ["calculator", "search"]
    assert resp.tool_calls[0].arguments == {"expression": "1+1"}
    # dict arguments (some servers send objects instead of JSON strings).
    assert resp.tool_calls[1].arguments == {"q": "tomo"}


async def test_http_error_raises_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(LLMRequestError):
        await _client(httpx.MockTransport(handler)).complete(
            [{"role": "user", "content": "hi"}]
        )


async def test_empty_choices_raises_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(LLMRequestError):
        await _client(httpx.MockTransport(handler)).complete(
            [{"role": "user", "content": "hi"}]
        )


async def test_request_error_wraps_network_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMRequestError):
        await _client(httpx.MockTransport(handler)).complete(
            [{"role": "user", "content": "hi"}]
        )


async def test_explicit_args_client() -> None:
    """Constructing with explicit args does not need env/config."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_body(content="ok"))

    client = OpenAICompatClient(
        base_url=_BASE,
        api_key=_KEY,
        model=_MODEL,
        transport=httpx.MockTransport(handler),
    )
    assert client.endpoint == f"{_BASE}/chat/completions"
    resp = await client.complete([{"role": "user", "content": "hi"}])
    assert resp.content == "ok"


def test_whitespace_only_api_key_raises() -> None:
    """A key of only spaces must be treated as missing after stripping."""
    with pytest.raises(LLMConfigError):
        OpenAICompatClient(api_key="   ", base_url=_BASE, model=_MODEL)


def test_base_url_already_including_chat_completions_unchanged() -> None:
    """A base_url that already ends with /chat/completions must not get a
    second /chat/completions appended."""
    client = OpenAICompatClient(
        api_key=_KEY,
        model=_MODEL,
        base_url="http://x/v1/chat/completions",
        transport=httpx.MockTransport(lambda req: httpx.Response(200)),
    )
    assert client.endpoint == "http://x/v1/chat/completions"


async def test_non_dict_json_arguments_coerced_to_dict() -> None:
    """JSON arguments that decode to a non-object (e.g. a list) must still
    leave ToolCall.arguments as a dict."""
    raw_tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "calculator", "arguments": "[1, 2]"},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_completion_body(content=None, tool_calls=raw_tool_calls)
        )

    resp = await _client(httpx.MockTransport(handler)).complete(
        [{"role": "user", "content": "hi"}]
    )
    assert len(resp.tool_calls) == 1
    assert isinstance(resp.tool_calls[0].arguments, dict)


async def test_malformed_choices_zero_raises_request_error() -> None:
    """choices[0] being null must raise LLMRequestError, not AttributeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [None]})

    with pytest.raises(LLMRequestError):
        await _client(httpx.MockTransport(handler)).complete(
            [{"role": "user", "content": "hi"}]
        )


async def test_non_dict_tool_call_entry_is_skipped() -> None:
    """Malformed (non-dict) tool_calls entries are skipped, not crashed on."""
    raw_tool_calls = [
        "not-a-dict",
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "calculator", "arguments": "{}"},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_completion_body(content=None, tool_calls=raw_tool_calls)
        )

    resp = await _client(httpx.MockTransport(handler)).complete(
        [{"role": "user", "content": "hi"}]
    )
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "calculator"


async def test_aclose_releases_client() -> None:
    """aclose() closes the underlying httpx client without error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_body(content="ok"))

    client = _client(httpx.MockTransport(handler))
    await client.complete([{"role": "user", "content": "hi"}])
    await client.aclose()


# ── JSON auto-repair tests ──────────────────────────────────────


def test_repair_valid_json_passes_through() -> None:
    assert _repair_json('{"command": "ls"}') == {"command": "ls"}


def test_repair_concatenated_duplicates_takes_first_valid() -> None:
    """The most common LLM malformation: the JSON object is emitted twice,
    the first copy broken, the second valid."""
    raw = '{"command": "ls", "timeout": 5, "workplace": "dev"}{"command": "ls", "timeout": 5, "workplace": "dev"}'
    result = _repair_json(raw)
    assert result is not None
    assert result.get("command") == "ls"
    assert result.get("timeout") == 5


def test_repair_missing_closing_quote_salvages_second_block() -> None:
    """Missing closing quote in the first block → scan finds the valid
    second block."""
    raw = '{"command": "ls, "timeout": 5}{"command": "ls", "timeout": 5}'
    result = _repair_json(raw)
    assert result is not None
    assert result.get("command") == "ls"
    assert result.get("timeout") == 5


def test_repair_trailing_comma_removed() -> None:
    assert _repair_json('{"command": "ls",}') == {"command": "ls"}


def test_repair_markdown_fences_stripped() -> None:
    raw = '```json\n{"command": "ls"}\n```'
    assert _repair_json(raw) == {"command": "ls"}


def test_repair_double_encoded_string() -> None:
    """When json.loads returns a string that is itself JSON."""
    raw = '"{\\"command\\": \\"ls\\"}"'
    assert _repair_json(raw) == {"command": "ls"}


def test_repair_returns_none_for_total_garbage() -> None:
    assert _repair_json("not json at all") is None


def test_repair_empty_string_returns_empty_dict() -> None:
    assert _repair_json("") == {}
    assert _repair_json("   ") == {}


def test_repair_user_real_world_example() -> None:
    """The exact malformed JSON the user reported — missing quote, doubled
    fields, concatenated duplicate."""
    raw = (
        '{"command": "ping -c 5 8.8.8.8, "timeout": 10, "workplace": ""local_dev", '
        '"timeout": 10, "workplace": "local_dev"}'
        '{"command": "ping -c 5 8.8.8.8", "timeout": 10, "workplace": "local_dev"}'
    )
    result = _repair_json(raw)
    assert result is not None
    assert result.get("command") == "ping -c 5 8.8.8.8"
    assert result.get("timeout") == 10
    assert result.get("workplace") == "local_dev"


# ── Parallel tool call streaming tests ──────────────────────────


async def test_parallel_tool_calls_streamed_correctly() -> None:
    """Two tool calls streamed with proper ``index`` → arguments are NOT
    concatenated into one buffer."""
    # Argument fragments for tool 0 (accumulates to {"command": "ls", "workplace": "dev"})
    f0a = json.dumps({"command": "ls"})[:-1]  # {"command": "ls"  (drop closing })
    f0b = ', "workplace": "dev"}'
    f0c = "}"
    # Argument fragments for tool 1 (accumulates to {"path": "/tmp"})
    f1a = json.dumps({"path": "/tmp"})[:-1]  # {"path": "/tmp"  (drop closing })
    f1c = "}"

    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "type": "function", "function": {"name": "bash", "arguments": ""}},
            {"index": 1, "id": "c2", "type": "function", "function": {"name": "read_file", "arguments": ""}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": f0a}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "function": {"arguments": f1a}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": f0b}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "function": {"arguments": f1c}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": f0c}},
        ]}}]},
    ]
    sse_body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    client = _client(httpx.MockTransport(handler))
    events = [ev async for ev in client.stream_complete([])]
    assert events[-1]["type"] == "done"
    resp = events[-1]["response"]
    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].name == "bash"
    assert resp.tool_calls[1].name == "read_file"
    assert resp.tool_calls[0].arguments == {"command": "ls", "workplace": "dev"}
    assert resp.tool_calls[1].arguments == {"path": "/tmp"}
    await client.aclose()


async def test_malformed_arguments_repaired_via_stream() -> None:
    """Tool call arguments with concatenated duplicate JSON are repaired
    via _repair_json when streaming."""
    args = json.dumps({"command": "ls"})
    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "type": "function", "function": {"name": "bash", "arguments": args}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": args}},
        ]}}]},
        {"choices": [{"delta": {}}]},
    ]
    sse_body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    client = _client(httpx.MockTransport(handler))
    events = [ev async for ev in client.stream_complete([])]
    resp = events[-1]["response"]
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].arguments == {"command": "ls"}
    await client.aclose()


# ── fetch_model_context_window ────────────────────────────────────


async def test_fetch_model_context_window_from_list() -> None:
    """fetch_model_context_window extracts context from /models list."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [
                    {"id": _MODEL, "max_model_len": 16384},
                    {"id": "other", "context_window": 4096},
                ]
            })
        return httpx.Response(404)

    client = _client(httpx.MockTransport(handler))
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 16384
    finally:
        await client.aclose()


async def test_fetch_model_context_window_network_error_returns_none() -> None:
    """Network failure returns None, not an exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(httpx.MockTransport(handler))
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx is None
    finally:
        await client.aclose()


async def test_fetch_model_context_window_no_match_returns_none() -> None:
    """Model not in /models list and no context field → None."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "other", "context_window": 4096}]})
        return httpx.Response(404)

    client = _client(httpx.MockTransport(handler))
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx is None
    finally:
        await client.aclose()


async def test_fetch_model_context_window_slash_suffix_match() -> None:
    """Model id 'org/gpt-4o' matches 'gpt-4o' via /{model} suffix."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": f"org/{_MODEL}", "context_window": 8192}]
            })
        return httpx.Response(404)

    client = _client(httpx.MockTransport(handler))
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 8192
    finally:
        await client.aclose()
