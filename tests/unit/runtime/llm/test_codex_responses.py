"""CodexResponsesClient HTTP mapping tests via httpx2.MockTransport.

No real network calls: a mock transport inspects the outgoing Responses-API
request and returns canned Responses-shaped JSON/SSE so we can verify the
wire mapping (content <-> LLMResponse, tool_calls <-> ToolCall).
"""

from __future__ import annotations

import json

import httpx2
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


def _client(transport: httpx2.MockTransport, **kw) -> CodexResponsesClient:
    return CodexResponsesClient(base_url=_BASE, access_token=_TOKEN, model=_MODEL, transport=transport, **kw)


def test_missing_token_raises_config_error() -> None:
    with pytest.raises(LLMConfigError):
        CodexResponsesClient(base_url=_BASE, access_token="", model=_MODEL)






@pytest.mark.asyncio
async def test_cache_key_survives_live_changes_and_client_recreation() -> None:
    client = _client(httpx2.MockTransport(lambda req: httpx2.Response(200)))
    other = _client(httpx2.MockTransport(lambda req: httpx2.Response(200)))
    try:
        first = [{"role": "system", "content": "stable instructions"},
                 {"role": "user", "content": "previous request"},
                 {"role": "system", "content": "clock one"}]
        second = [*first[:2], {"role": "assistant", "content": "previous answer"},
                  {"role": "system", "content": "clock two"}]
        key = client._payload(first, None)["prompt_cache_key"]
        assert client._payload(first, None)["extra_headers"] == {"session-id": key}
        assert key == other._payload(second, None)["prompt_cache_key"]
        assert len(key) == 64
        changed = [{"role": "system", "content": "different instructions"}]
        assert key != client._payload(changed, None)["prompt_cache_key"]
    finally:
        await client.aclose()
        await other.aclose()




def test_messages_to_responses_input_translates_image_parts() -> None:
    """``image_url`` parts on user messages become Responses ``input_image``."""
    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        },
    ]
    _, items = _messages_to_responses_input(messages)
    assert items == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "what is this?"},
                {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
            ],
        }
    ]


def test_messages_to_responses_input_text_only_list_stays_flat() -> None:
    """A list-form message without images keeps the plain-string path."""
    messages = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
        }
    ]
    _, items = _messages_to_responses_input(messages)
    assert items == [{"role": "user", "content": "hello"}]




def _completed_sse(response: dict) -> httpx2.Response:
    events = [{"type": "response.output_item.done", "item": item}
              for item in response.get("output", [])]
    events.append({"type": "response.completed", "response": response})
    return httpx2.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
async def test_public_api_key_uses_responses_wire_format() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["store"] is False
        assert body["reasoning"]["summary"] == "auto"
        assert "session-id" not in request.headers
        return _completed_sse({"id": "resp_1", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}
        ]})

    client = CodexResponsesClient(base_url="https://api.openai.com/v1", access_token="sk-test",
                                  model="gpt-5", transport=httpx2.MockTransport(handler))
    try:
        assert (await client.complete([{"role": "user", "content": "hi"}])).content == "ok"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_returns_text() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert body["model"] == _MODEL
        assert body["stream"] is True
        assert body["store"] is False
        assert body["instructions"] == "sys"
        assert request.headers["session-id"] == body["prompt_cache_key"]
        return _completed_sse({
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": "hello there"}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            })

    client = _client(httpx2.MockTransport(handler))
    resp = await client.complete([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    assert isinstance(resp, LLMResponse)
    assert resp.content == "hello there"
    assert resp.tool_calls == []
    assert resp.prompt_tokens == 10
    assert resp.completion_tokens == 5


@pytest.mark.asyncio
async def test_complete_returns_tool_calls() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _completed_sse({
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call", "id": "fc_1", "call_id": "call_1",
                        "name": "bash", "arguments": '{"cmd":"ls"}', "status": "completed",
                    }
                ],
            })

    client = _client(httpx2.MockTransport(handler))
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
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": {"message": "bad token", "code": "invalid_api_key"}})

    client = _client(httpx2.MockTransport(handler))
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

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})

    client = _client(httpx2.MockTransport(handler))
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

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = json.loads(request.content)
        return _completed_sse({"id": "resp_1", "status": "completed", "output": [
                {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "hi"}]},
            ]})

    client = _client(httpx2.MockTransport(handler), reasoning_effort="high")
    await client.complete([{"role": "user", "content": "hi"}])
    assert captured["body"]["reasoning"] == {"effort": "high", "summary": "auto"}


@pytest.mark.asyncio
async def test_complete_clamps_minimal_effort_to_low() -> None:
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = json.loads(request.content)
        return _completed_sse({"id": "resp_1", "status": "completed", "output": [
                {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "hi"}]},
            ]})

    client = _client(httpx2.MockTransport(handler), reasoning_effort="minimal")
    await client.complete([{"role": "user", "content": "hi"}])
    assert captured["body"]["reasoning"] == {"effort": "low", "summary": "auto"}


@pytest.mark.asyncio
async def test_opencode_preserves_catalog_minimal_effort() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert body["reasoning"]["effort"] == "minimal"
        return _completed_sse({"id": "resp_1", "status": "completed", "output": [
            {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
             "content": [{"type": "output_text", "text": "hi"}]},
        ]})

    client = CodexResponsesClient(
        base_url="https://opencode.ai/zen/go/v1", access_token=_TOKEN,
        model="muse-spark-1.3-contributor", reasoning_effort="minimal",
        transport=httpx2.MockTransport(handler),
    )
    try:
        await client.complete([{"role": "user", "content": "hi"}])
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_omits_reasoning_when_not_configured() -> None:
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = json.loads(request.content)
        return _completed_sse({"id": "resp_1", "status": "completed", "output": [
                {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "hi"}]},
            ]})

    client = _client(httpx2.MockTransport(handler))
    await client.complete([{"role": "user", "content": "hi"}])
    assert "reasoning" not in captured["body"]


@pytest.mark.asyncio
async def test_complete_extracts_reasoning_summary() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _completed_sse({
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "reasoning", "id": "rs_1", "encrypted_content": "opaque-blob",
                        "summary": [{"type": "summary_text", "text": "Thinking about the ask."}],
                    },
                    {
                        "type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": "Here's the answer."}],
                    },
                ],
            })

    client = _client(httpx2.MockTransport(handler))
    resp = await client.complete([{"role": "user", "content": "hi"}])
    assert resp.content == "Here's the answer."
    assert resp.reasoning == "Thinking about the ask."


@pytest.mark.asyncio
async def test_complete_reasoning_is_none_when_absent() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _completed_sse({"id": "resp_1", "status": "completed", "output": [
                {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "hi"}]},
            ]})

    client = _client(httpx2.MockTransport(handler))
    resp = await client.complete([{"role": "user", "content": "hi"}])
    assert resp.reasoning is None


@pytest.mark.asyncio
async def test_stream_complete_exposes_reasoning_before_answer() -> None:
    events = [
        {"type": "response.reasoning_summary_text.delta", "delta": "Working "},
        {"type": "response.reasoning_summary_text.delta", "delta": "it out."},
        {"type": "response.output_item.done", "item": {"type": "reasoning", "id": "rs_1", "summary": [
            {"type": "summary_text", "text": "Working it out."}]}},
        {"type": "response.output_text.delta", "delta": "answer"},
        {"type": "response.completed", "response": {"id": "resp_1", "status": "completed"}},
    ]
    client = _client(httpx2.MockTransport(lambda request: httpx2.Response(
        200, content=_sse(events), headers={"content-type": "text/event-stream"})))
    try:
        output = [ev async for ev in client.stream_complete([{"role": "user", "content": "hi"}])]
        assert [ev["type"] for ev in output] == ["reasoning_delta", "reasoning_delta", "delta", "done"]
        assert output[-1]["response"].reasoning == "Working it out."
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_complete_separates_reasoning_summary_parts() -> None:
    events = [
        {"type": "response.reasoning_summary_text.delta", "item_id": "rs_1", "summary_index": 0, "delta": "**Checking "},
        {"type": "response.reasoning_summary_text.delta", "item_id": "rs_1", "summary_index": 0, "delta": "memory.**"},
        {"type": "response.reasoning_summary_text.delta", "item_id": "rs_1", "summary_index": 1, "delta": "**Listing it.**"},
        {"type": "response.output_item.done", "item": {"type": "reasoning", "id": "rs_1", "summary": [
            {"type": "summary_text", "text": "**Checking memory.**"},
            {"type": "summary_text", "text": "**Listing it.**"}]}},
        {"type": "response.output_text.delta", "delta": "answer"},
        {"type": "response.completed", "response": {"id": "resp_1", "status": "completed"}},
    ]
    client = _client(httpx2.MockTransport(lambda request: httpx2.Response(
        200, content=_sse(events), headers={"content-type": "text/event-stream"})))
    try:
        output = [ev async for ev in client.stream_complete([{"role": "user", "content": "hi"}])]
        streamed = "".join(ev["content"] for ev in output if ev["type"] == "reasoning_delta")
        assert streamed == "**Checking memory.**\n\n**Listing it.**"
        assert output[-1]["response"].reasoning == streamed
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_complete_extracts_reasoning_summary() -> None:
    events = [
        {
            "type": "response.output_item.done",
            "item": {
                "type": "reasoning", "id": "rs_1", "encrypted_content": "opaque-blob",
                "summary": [{"type": "summary_text", "text": "Working it out."}],
            },
        },
        {"type": "response.output_text.delta", "delta": "answer"},
        {"type": "response.completed", "response": {"id": "resp_1", "status": "completed"}},
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})

    client = _client(httpx2.MockTransport(handler))
    final = None
    async for ev in client.stream_complete([{"role": "user", "content": "hi"}]):
        if ev["type"] == "done":
            final = ev["response"]
    assert final.content == "answer"
    assert final.reasoning == "Working it out."


@pytest.mark.asyncio
async def test_complete_collects_deltas_reasoning_tools_and_usage() -> None:
    events = [
        {"type": "response.reasoning_summary_text.delta", "delta": "Thinking"},
        {"type": "response.output_text.delta", "delta": "Checking "},
        {"type": "response.output_text.delta", "delta": "files"},
        {"type": "response.output_item.done", "item": {
            "type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "Thinking"}]}},
        {"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": "call_1", "name": "bash", "arguments": '{"cmd":"ls"}'}},
        {"type": "response.completed", "response": {
            "id": "resp_1", "status": "completed", "usage": {"input_tokens": 12, "output_tokens": 7,
                        "input_tokens_details": {"cached_tokens": 9},
                        "output_tokens_details": {"reasoning_tokens": 3}}}},
    ]

    def handler(request):
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["tools"][0]["name"] == "bash"
        return httpx2.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})

    client = _client(httpx2.MockTransport(handler))
    try:
        resp = await client.complete([{"role": "user", "content": "check files"}], tools=[
            {"type": "function", "function": {"name": "bash"}}])
        assert resp.content == "Checking files"
        assert resp.reasoning == "Thinking"
        assert resp.tool_calls[0].id == "call_1"
        assert resp.tool_calls[0].arguments == {"cmd": "ls"}
        assert (resp.prompt_tokens, resp.completion_tokens) == (12, 7)
        assert resp.cached_tokens == 9
        assert resp.reasoning_tokens == 3
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['complete', 'stream_complete'])
@pytest.mark.parametrize(('terminal', 'error'), [
    (None, 'without a completion'),
    ({'type': 'response.failed', 'response': {'error': {'message': 'backend failed'}}}, 'backend failed'),
    ({'type': 'response.incomplete', 'response': {'incomplete_details': {'reason': 'max_output_tokens'}}}, 'max_output_tokens'),
    ({'type': 'error', 'message': 'backend error', 'code': 'server_error'}, 'backend error'),
])
async def test_partial_stream_is_not_accepted_as_success(method, terminal, error):
    events = [{'type': 'response.output_text.delta', 'delta': 'partial output'}]
    if terminal:
        events.append(terminal)
    client = _client(httpx2.MockTransport(lambda request: httpx2.Response(
        200, content=_sse(events), headers={'content-type': 'text/event-stream'})))
    try:
        with pytest.raises(LLMRequestError, match=error):
            if method == 'complete':
                await client.complete([{'role': 'user', 'content': 'hi'}])
            else:
                async for event in client.stream_complete([{'role': 'user', 'content': 'hi'}]):
                    assert event['type'] != 'done'
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_memory_extraction_and_session_title_use_codex_stream(tmp_path):
    import sqlite3
    from app.runtime.memory.vault.extract import extract_turn, entity_context
    from app.runtime.session_title import generate_session_title

    requests = []
    replies = [json.dumps([{'entity': 'person/max-verstappen', 'fact': 'The user’s favorite F1 driver.'}]),
               'Favorite F1 Driver']

    def handler(request):
        body = json.loads(request.content)
        assert body['stream'] is True
        assert request.url.path == '/backend-api/codex/responses'
        requests.append(body)
        return _completed_sse({'id': 'resp_1', 'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': replies.pop(0)}]}]})

    client = _client(httpx2.MockTransport(handler), reasoning_effort='low')
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    try:
        assert await extract_turn('alice', 'ses_1', 'My favorite F1 driver is Max Verstappen.',
                                  'Got it.', client, home_root=tmp_path, conn=conn) == 1
        assert entity_context('alice', home_root=tmp_path)['person/max-verstappen'] == ['The user’s favorite F1 driver.']
        assert await generate_session_title('Favorite driver', 'Max Verstappen', llm=client) == 'Favorite F1 Driver'
        assert len(requests) == 2
    finally:
        conn.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_cancellation_closes_http_stream():
    import asyncio

    started = asyncio.Event()
    closed = asyncio.Event()

    class WaitingStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield _sse([{'type': 'response.output_text.delta', 'delta': 'partial'}])
            started.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    client = _client(httpx2.MockTransport(lambda request: httpx2.Response(
        200, stream=WaitingStream(), headers={'content-type': 'text/event-stream'})))
    task = asyncio.create_task(client.complete([{'role': 'user', 'content': 'hi'}]))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await client.aclose()
