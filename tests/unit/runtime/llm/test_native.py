"""Native provider streams preserve tools/images and fail on truncation."""

import json

import httpx2
import pytest

from app.runtime.llm.native import NativeMessagesClient
from app.runtime.llm.openai_compat import LLMRequestError


@pytest.mark.parametrize("base_url,model,native", [
    ("https://api.anthropic.com/v1", "claude-opus-5-5", True),
    ("https://api.anthropic.com/v1", "claude-opus-4-8", True),
    ("https://api.anthropic.com/v1", "claude-opus-4-6", False),
    ("https://api.anthropic.com/v1", "claude-sonnet-5", False),
    ("https://opencode.ai/zen/v1", "claude-opus-5-5", False),
])
async def test_claude_inline_discovery_is_gated_and_preserves_tool_prefix(base_url, model, native):
    from app.runtime.tools.discovery import ToolDiscovery, bind, reset

    requests, headers = [], []

    def wire(request):
        requests.append(json.loads(request.content))
        headers.append(dict(request.headers))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text='data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n\n'
                                    'data: {"type":"message_stop"}\n\n')

    client = NativeMessagesClient(base_url=base_url, api_key="token", model=model,
                                  protocol="messages", timeout=30, transport=httpx2.MockTransport(wire))
    schemas = [{"type": "function", "function": {
        "name": name, "description": name,
        "parameters": {"type": "object", "properties": {}},
    }} for name in ("search_tools", "lookup")]
    history = [{"role": "system", "content": "Stable instructions"},
               {"role": "user", "content": "Look up the record"}]
    searched = [*history,
                {"role": "assistant", "tool_calls": [{"id": "search", "function": {
                    "name": "search_tools", "arguments": '{"query":"lookup"}',
                }}]},
                {"role": "tool", "tool_call_id": "search",
                 "content": json.dumps({"loaded_tools": ["lookup", "unassigned_secret"]})}]
    token = bind(ToolDiscovery(schemas))
    try:
        await client.complete(history, schemas[:1])
        await client.complete(searched, schemas)
        await client.complete(searched, schemas[:1])
    finally:
        reset(token)
        await client.aclose()
    additions = [b for m in requests[1]["messages"] for b in m["content"] if b["type"] == "tool_addition"]
    assert requests[0]["system"] == requests[1]["system"]
    if native:
        assert requests[0]["tools"] == requests[1]["tools"]
        assert additions == [{"type": "tool_addition", "tool": {"type": "tool_definition", "definition": {
            "name": "lookup", "description": "lookup", "input_schema": {"type": "object", "properties": {}},
        }}}]
        assert headers[1]["anthropic-beta"] == "inline-tools-2026-09-15"
        assert requests[1]["messages"][-1]["role"] == "system"
        assert requests[1]["messages"][-2]["content"][0]["type"] == "tool_result"
        assert not any(b["type"] == "tool_addition" for m in requests[2]["messages"] for b in m["content"])
    else:
        assert not additions
        assert "anthropic-beta" not in headers[1]
        assert {t["name"] for t in requests[1]["tools"]} == {"search_tools", "lookup"}


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["messages", "google"])
async def test_native_tool_roundtrip_and_truncated_stream(protocol):
    requests = []
    truncated = False

    def wire(request):
        body = json.loads(request.content)
        requests.append(body)
        if protocol == "messages":
            events = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "call-one",
                        "name": "lookup",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"q":"hello"}',
                    },
                },
                {"type": "message_stop"},
            ]
        else:
            events = [
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "lookup",
                                            "args": {"q": "hello"},
                                        },
                                        "thoughtSignature": "signed-thought",
                                    }
                                ]
                            }
                        }
                    ]
                },
                {"candidates": [{"finishReason": "STOP"}]},
            ]
        if truncated:
            events.pop()
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(e) + "\n\n" for e in events),
        )

    client = NativeMessagesClient(
        base_url="https://opencode.ai/zen/v1",
        api_key="token",
        model="native-model",
        protocol=protocol,
        timeout=30,
        transport=httpx2.MockTransport(wire),
    )
    client.context_window = 4096
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            },
        }
    ]
    history = [
        {"role": "system", "content": "Be helpful"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                },
            ],
        },
    ]
    try:
        response = await client.complete(history, tools)
        output_limit = requests[0].get("max_tokens") if protocol == "messages" else requests[0]["generationConfig"]["maxOutputTokens"]
        assert output_limit == 1024
        call = response.tool_calls[0]
        assert call.name == "lookup" and call.arguments == {"q": "hello"}
        history.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call.id, "content": "found"},
            ]
        )
        client.max_output_tokens = 123
        await client.complete(history, tools)
        output_limit = requests[1].get("max_tokens") if protocol == "messages" else requests[1]["generationConfig"]["maxOutputTokens"]
        assert output_limit == 123
        if protocol == "messages":
            assert (
                requests[0]["messages"][0]["content"][1]["source"]["media_type"]
                == "image/png"
            )
            assert requests[1]["messages"][-1]["content"][0]["tool_use_id"] == call.id
        else:
            assert (
                requests[0]["contents"][0]["parts"][1]["inlineData"]["mimeType"]
                == "image/png"
            )
            assert (
                requests[1]["contents"][1]["parts"][0]["thoughtSignature"]
                == "signed-thought"
            )
            assert (
                requests[1]["contents"][-1]["parts"][0]["functionResponse"]["name"]
                == "lookup"
            )
        truncated = True
        with pytest.raises(LLMRequestError, match="before completion"):
            await client.complete(history, tools)
    finally:
        await client.aclose()


@pytest.mark.parametrize("protocol", ["messages", "google"])
async def test_live_context_stays_after_stable_history_on_wire(protocol):
    requests = []

    def wire(request):
        requests.append(json.loads(request.content))
        events = ([{"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
                   {"type": "message_stop"}] if protocol == "messages"
                  else [{"candidates": [{"finishReason": "STOP"}]}])
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text="".join("data: " + json.dumps(e) + "\n\n" for e in events))

    client = NativeMessagesClient(base_url="https://native.test/v1", api_key="token",
                                  model="native-model", protocol=protocol, timeout=30,
                                  transport=httpx2.MockTransport(wire))
    history = [
        {"role": "system", "content": "Stable instructions"},
        {"role": "user", "content": "Earlier request"},
        {"role": "assistant", "content": "Earlier reply"},
        {"role": "system", "content": "Time: 10:00; retrieved memory A"},
        {"role": "user", "content": "Continue"},
    ]
    tools = [{"type": "function", "function": {
        "name": name, "parameters": {"type": "object", "properties": {}},
    }} for name in ("z_tool", "a_tool")]
    try:
        await client.complete(history, tools)
        changed = [dict(m) for m in history]
        changed[3]["content"] = "Time: 11:00; retrieved memory B"
        await client.complete(changed, list(reversed(tools)))
    finally:
        await client.aclose()
    if protocol == "messages":
        assert requests[0]["system"] == requests[1]["system"]
        assert requests[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}
        assert requests[0]["messages"][:2] == requests[1]["messages"][:2]
        assert requests[0]["messages"][-1]["content"][0]["text"].startswith("[Runtime context]\nTime:")
        assert requests[0]["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert [t["name"] for t in requests[0]["tools"]] == ["a_tool", "z_tool"]
    else:
        assert requests[0]["systemInstruction"] == requests[1]["systemInstruction"]
        assert requests[0]["systemInstruction"] == {"parts": [{"text": "Stable instructions"}]}
        assert requests[0]["contents"][:2] == requests[1]["contents"][:2]
        assert requests[0]["contents"][-1]["parts"][0]["text"].startswith("[Runtime context]\nTime:")
        assert "cache_control" not in json.dumps(requests)
    assert requests[0]["tools"] == requests[1]["tools"]
    assert "cache_control" not in json.dumps(history)
