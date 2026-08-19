"""CodexResponsesClient HTTP mapping tests via httpx.MockTransport.

No real network calls: a mock transport inspects the outgoing Responses-API
request and returns canned Responses-shaped JSON/SSE so we can verify the
wire mapping (content <-> LLMResponse, tool_calls <-> ToolCall).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.runtime.llm.base import LLMResponse
from app.runtime.llm.codex_responses import (
    CodexResponsesClient,
    _messages_to_responses_input,
    _responses_tools,
)
from app.runtime.llm.openai_compat import LLMConfigError, LLMRequestError

_BASE = "https://chatgpt.com/backend-api/codex"
_TOKEN = "at-test"
_MODEL = "gpt-5-codex"


def _client(transport: httpx.MockTransport, **kw) -> CodexResponsesClient:
    return CodexResponsesClient(base_url=_BASE, access_token=_TOKEN, model=_MODEL, transport=transport, **kw)


def test_missing_token_raises_config_error() -> None:
    with pytest.raises(LLMConfigError):
        CodexResponsesClient(base_url=_BASE, access_token="", model=_MODEL)


def test_messages_to_responses_input_splits_system_as_instructions() -> None:
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
    ]
    instructions, items = _messages_to_responses_input(messages)
    assert instructions == "You are helpful."
    assert items == [{"role": "user", "content": "hi"}]


def test_messages_to_responses_input_converts_tool_calls_and_results() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run ls"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": '{"cmd":"ls"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file1\nfile2"},
    ]
    _, items = _messages_to_responses_input(messages)
    assert items[0] == {"role": "user", "content": "run ls"}
    assert items[1] == {
        "type": "function_call", "call_id": "call_1", "name": "bash", "arguments": '{"cmd":"ls"}'
    }
    assert items[2] == {"type": "function_call_output", "call_id": "call_1", "output": "file1\nfile2"}


def test_responses_tools_converts_function_schema() -> None:
    tools = [{"type": "function", "function": {"name": "bash", "description": "run", "parameters": {"type": "object"}}}]
    converted = _responses_tools(tools)
    assert converted == [
        {"type": "function", "name": "bash", "description": "run", "parameters": {"type": "object"}}
    ]


@pytest.mark.asyncio
async def test_complete_returns_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == _MODEL
        assert body["store"] is False
        assert body["instructions"] == "sys"
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": "hello there"}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    client = _client(httpx.MockTransport(handler))
    resp = await client.complete([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    assert isinstance(resp, LLMResponse)
    assert resp.content == "hello there"
    assert resp.tool_calls == []
    assert resp.prompt_tokens == 10
    assert resp.completion_tokens == 5


@pytest.mark.asyncio
async def test_complete_returns_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call", "id": "fc_1", "call_id": "call_1",
                        "name": "bash", "arguments": '{"cmd":"ls"}', "status": "completed",
                    }
                ],
            },
        )

    client = _client(httpx.MockTransport(handler))
    resp = await client.complete(
        [{"role": "user", "content": "run ls"}],
        tools=[{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}],
    )
    assert resp.content is None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "bash"
    assert resp.tool_calls[0].arguments == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_complete_raises_llm_request_error_on_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad token", "code": "invalid_api_key"}})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(LLMRequestError):
        await client.complete([{"role": "user", "content": "hi"}])


def _sse(events: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


@pytest.mark.asyncio
async def test_stream_complete_yields_deltas_then_done() -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
        {"type": "response.output_text.delta", "delta": "hel"},
        {"type": "response.output_text.delta", "delta": "lo"},
        {
            "type": "response.output_item.done",
            "item": {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                      "content": [{"type": "output_text", "text": "hello"}]},
        },
        {
            "type": "response.completed",
            "response": {"id": "resp_1", "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 2}},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})

    client = _client(httpx.MockTransport(handler))
    deltas = []
    final = None
    async for ev in client.stream_complete([{"role": "user", "content": "hi"}]):
        if ev["type"] == "delta":
            deltas.append(ev["content"])
        else:
            final = ev["response"]
    assert "".join(deltas) == "hello"
    assert final.content == "hello"
    assert final.prompt_tokens == 3
    assert final.completion_tokens == 2


@pytest.mark.asyncio
async def test_complete_sends_reasoning_effort() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": "resp_1", "status": "completed", "output": [
                {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "hi"}]},
            ]},
        )

    client = _client(httpx.MockTransport(handler), reasoning_effort="high")
    await client.complete([{"role": "user", "content": "hi"}])
    assert captured["body"]["reasoning"] == {"effort": "high", "summary": "auto"}


@pytest.mark.asyncio
async def test_complete_clamps_minimal_effort_to_low() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": "resp_1", "status": "completed", "output": [
                {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "hi"}]},
            ]},
        )

    client = _client(httpx.MockTransport(handler), reasoning_effort="minimal")
    await client.complete([{"role": "user", "content": "hi"}])
    assert captured["body"]["reasoning"] == {"effort": "low", "summary": "auto"}


@pytest.mark.asyncio
async def test_complete_omits_reasoning_when_not_configured() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": "resp_1", "status": "completed", "output": [
                {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "hi"}]},
            ]},
        )

    client = _client(httpx.MockTransport(handler))
    await client.complete([{"role": "user", "content": "hi"}])
    assert "reasoning" not in captured["body"]
