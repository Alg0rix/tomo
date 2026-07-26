"""Deterministic mock LLM client tests."""

from __future__ import annotations

from app.runtime.llm.base import LLMClient, LLMResponse
from app.runtime.llm.mock import MockLLMClient


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _assistant_tool_call(call_id: str, expr: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "calculator",
                    "arguments": f'{{"expression": "{expr}"}}',
                },
            }
        ],
    }


def _tool_result(call_id: str, result: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": result}


def test_mock_satisfies_llm_client_protocol() -> None:
    assert isinstance(MockLLMClient(), LLMClient)


async def test_plain_message_returns_default_content() -> None:
    resp = await MockLLMClient().complete([_user("hello")])
    assert isinstance(resp, LLMResponse)
    assert resp.content
    assert resp.tool_calls == []
    assert not resp.has_tool_calls


async def test_no_user_message_returns_default() -> None:
    resp = await MockLLMClient().complete([{"role": "system", "content": "be helpful"}])
    assert resp.content
    assert resp.tool_calls == []


async def test_calculate_keyword_triggers_calculator_tool_call() -> None:
    resp = await MockLLMClient().complete([_user("calculate 2 + 2")])
    assert resp.content is None
    assert resp.has_tool_calls
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.name == "calculator"
    assert call.arguments == {"expression": "2 + 2"}


async def test_equals_sign_triggers_calculator_tool_call() -> None:
    resp = await MockLLMClient().complete([_user("what is 3 * 4 =")])
    assert resp.content is None
    assert resp.tool_calls[0].name == "calculator"
    assert resp.tool_calls[0].arguments["expression"] == "3 * 4"


async def test_parenthesised_expression_extracted() -> None:
    resp = await MockLLMClient().complete([_user("calculate 3 * (4 + 5)")])
    assert resp.tool_calls[0].arguments["expression"] == "3 * (4 + 5)"


async def test_tool_result_message_yields_final_content() -> None:
    messages = [
        _user("calculate 2 + 2"),
        _assistant_tool_call("call_mock_calculator", "2 + 2"),
        _tool_result("call_mock_calculator", "4"),
    ]
    resp = await MockLLMClient().complete(messages)
    assert resp.content
    assert resp.tool_calls == []


async def test_two_step_calculator_flow_matches_agent_loop() -> None:
    """First call -> calculator tool call; second call (with tool result)
    -> final text. This is the exact shape the agent loop will rely on."""
    client = MockLLMClient()

    first = await client.complete([_user("calculate 7 - 3")])
    assert first.has_tool_calls
    assert first.tool_calls[0].name == "calculator"
    expr = first.tool_calls[0].arguments["expression"]
    assert expr == "7 - 3"

    messages = [
        _user("calculate 7 - 3"),
        _assistant_tool_call("call_mock_calculator", expr),
        _tool_result("call_mock_calculator", "4"),
    ]
    second = await client.complete(messages)
    assert second.content
    assert not second.has_tool_calls


async def test_tool_result_takes_precedence_over_calc_keyword() -> None:
    """Even if the latest user text still mentions calculate, a tool result
    in the conversation means we are on the final step."""
    messages = [
        _user("calculate 2 + 2"),
        _assistant_tool_call("call_mock_calculator", "2 + 2"),
        _tool_result("call_mock_calculator", "4"),
        _user("calculate 5 + 5"),  # follow-up while result present
    ]
    # The tool result from the first turn is still in history, so the mock
    # should answer with final text rather than emit another tool call.
    resp = await MockLLMClient().complete(messages)
    assert resp.content
    assert resp.tool_calls == []
