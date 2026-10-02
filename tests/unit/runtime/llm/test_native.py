"""Native provider streams preserve tools/images and fail on truncation."""

import json

import httpx2
import pytest

from app.runtime.llm.native import NativeMessagesClient
from app.runtime.llm.openai_compat import LLMRequestError


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
        await client.complete(history, tools)
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
