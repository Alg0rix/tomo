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


async def test_default_config_resolves_lazily(monkeypatch) -> None:
    """Constructing without explicit args reads live config values."""
    from app.core import config

    monkeypatch.setattr(config, "LLM_BASE_URL", _BASE)
    monkeypatch.setattr(config, "LLM_API_KEY", _KEY)
    monkeypatch.setattr(config, "LLM_MODEL", _MODEL)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_body(content="ok"))

    client = OpenAICompatClient(transport=httpx.MockTransport(handler))
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
