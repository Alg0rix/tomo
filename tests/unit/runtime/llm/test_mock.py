"""Deterministic mock LLM client tests."""

from __future__ import annotations

from app.runtime.llm.base import LLMClient, LLMResponse
from app.runtime.llm.mock import MockLLMClient, _BASH_FINAL, _DEFAULT_REPLY


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _assistant_tool_call(call_id: str, command: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "bash",
                    "arguments": f'{{"command": "{command}"}}',
                },
            }
        ],
    }


def _tool_result(call_id: str, result: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": result}


_BASH_TOOLS = [{"type": "function", "function": {"name": "bash"}}]


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


async def test_run_keyword_triggers_bash_tool_call() -> None:
    resp = await MockLLMClient().complete([_user("run: echo hello")], tools=_BASH_TOOLS)
    assert resp.content is None
    assert resp.has_tool_calls
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.name == "bash"
    assert call.arguments == {"command": "echo hello"}


async def test_run_keyword_case_insensitive() -> None:
    resp = await MockLLMClient().complete([_user("Please RUN: pwd")], tools=_BASH_TOOLS)
    assert resp.tool_calls[0].name == "bash"
    assert resp.tool_calls[0].arguments["command"] == "pwd"


async def test_tool_result_message_yields_final_content() -> None:
    messages = [
        _user("run: echo 4"),
        _assistant_tool_call("call_mock_bash", "echo 4"),
        _tool_result("call_mock_bash", "4"),
    ]
    resp = await MockLLMClient().complete(messages)
    assert resp.content == _BASH_FINAL
    assert resp.tool_calls == []


async def test_two_step_bash_flow_matches_agent_loop() -> None:
    """First call -> bash tool call; second call (with tool result)
    -> final text. This is the exact shape the agent loop will rely on."""
    client = MockLLMClient()

    first = await client.complete([_user("run: echo hi")], tools=_BASH_TOOLS)
    assert first.has_tool_calls
    assert first.tool_calls[0].name == "bash"
    cmd = first.tool_calls[0].arguments["command"]
    assert cmd == "echo hi"

    messages = [
        _user("run: echo hi"),
        _assistant_tool_call("call_mock_bash", cmd),
        _tool_result("call_mock_bash", "hi"),
    ]
    second = await client.complete(messages, tools=_BASH_TOOLS)
    assert second.content == _BASH_FINAL
    assert not second.has_tool_calls


async def test_new_user_message_re_triggers_bash_after_tool_result() -> None:
    """A fresh user message following an earlier tool result must be able to
    trigger a new bash tool call. Historical tool results must NOT
    suppress future bash turns."""
    messages = [
        _user("run: echo 2"),
        _assistant_tool_call("call_mock_bash", "echo 2"),
        _tool_result("call_mock_bash", "2"),
        _user("run: echo 5"),  # new turn -> new tool call
    ]
    resp = await MockLLMClient().complete(messages, tools=_BASH_TOOLS)
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "bash"
    assert resp.tool_calls[0].arguments["command"] == "echo 5"


async def test_multi_turn_new_user_re_triggers_bash() -> None:
    """After a completed bash turn, a brand-new user run: message must
    trigger a fresh bash tool call rather than short-circuit to final text."""
    messages = [
        _user("run: echo 2"),
        _assistant_tool_call("call_mock_bash", "echo 2"),
        _tool_result("call_mock_bash", "2"),
        {"role": "assistant", "content": _BASH_FINAL},
        _user("run: echo 3"),  # new turn
    ]
    resp = await MockLLMClient().complete(messages, tools=_BASH_TOOLS)
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "bash"
    assert resp.tool_calls[0].arguments["command"] == "echo 3"


async def test_run_prompt_without_tools_returns_no_tool_calls() -> None:
    """When no tools are advertised the mock must not emit bash tool
    calls even for run: prompts (it returns the default reply)."""
    resp = await MockLLMClient().complete([_user("run: echo 2")], tools=None)
    assert resp.tool_calls == []
    assert resp.content == _DEFAULT_REPLY


async def test_run_prompt_with_bash_schema_emits_tool_call() -> None:
    """With a bash tool advertised, a run: prompt emits a tool call."""
    resp = await MockLLMClient().complete(
        [_user("run: echo 2")], tools=_BASH_TOOLS
    )
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "bash"


async def test_run_prompt_with_non_bash_tools_returns_no_tool_calls() -> None:
    """Tools that do not include bash must not trigger a bash tool call."""
    resp = await MockLLMClient().complete(
        [_user("run: echo 2")],
        tools=[{"type": "function", "function": {"name": "search"}}],
    )
    assert resp.tool_calls == []
    assert resp.content == _DEFAULT_REPLY


_RECALL_TOOLS = [{"type": "function", "function": {"name": "recall"}}]


async def test_vendor_deadline_triggers_recall_tool_call() -> None:
    resp = await MockLLMClient().complete(
        [_user("What is the Q3 vendor onboarding deadline?")],
        tools=_RECALL_TOOLS,
    )
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "recall"
    assert "vendor" in resp.tool_calls[0].arguments["query"].lower() or (
        "deadline" in resp.tool_calls[0].arguments["query"].lower()
    )


async def test_recall_keyword_triggers_recall() -> None:
    resp = await MockLLMClient().complete(
        [_user("recall support hours")], tools=_RECALL_TOOLS
    )
    assert resp.tool_calls[0].name == "recall"
    assert resp.tool_calls[0].arguments["query"] == "support hours"
