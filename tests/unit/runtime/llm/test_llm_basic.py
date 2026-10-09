"""Consolidated tests (merged from: test_factory.py, test_mock.py).
- test_factory.py: ``get_llm()`` factory — settings-backed OpenAI-compatible client only.
- test_mock.py: Deterministic mock LLM client tests.
"""

from __future__ import annotations

import pytest
from app.runtime.llm import OpenAICompatClient, get_llm
from app.runtime.llm.openai_compat import LLMConfigError
from app.services import store
from app.runtime.llm.base import LLMClient
from app.runtime.llm.mock import MockLLMClient, _BASH_FINAL, _DEFAULT_REPLY


# --- from test_factory.py ---
def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "llm-factory.db")


def test_get_llm_raises_without_profile(tmp_path) -> None:
    _rebind(tmp_path)
    with pytest.raises(LLMConfigError, match="System"):
        get_llm()


def _profile(pid: str, base: str, model: str) -> dict:
    return {"id": pid, "name": pid, "base_url": base, "api_key": "sk-" + pid, "model": model}


def test_get_llm_builds_client_from_default_profile(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(_profile("default", "https://example.test/v1", "gpt-test"))
    store.set_default_llm_profile("default")
    client = get_llm()
    assert isinstance(client, OpenAICompatClient)
    assert client.endpoint == "https://example.test/v1/chat/completions"


async def test_first_party_anthropic_profile_uses_native_messages(tmp_path):
    from app.runtime.llm.native import NativeMessagesClient

    _rebind(tmp_path)
    store.create_llm_profile(_profile("anthropic", "https://api.anthropic.com/v1", "claude-opus-5-5"))
    store.set_default_llm_profile("anthropic")
    client = get_llm()
    try:
        assert isinstance(client, NativeMessagesClient)
        assert client._protocol == "messages"
        assert client.context_profile["model"] == "claude-opus-5-5"
    finally:
        await client.aclose()


def test_get_llm_maps_requested_effort_to_profile_value(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {
            **_profile("default", "https://example.test/v1", "gpt-test"),
            "reasoning_efforts": ["balanced", "provider-max"],
        }
    )
    store.set_default_llm_profile("default")

    client = get_llm(reasoning_effort="balanced")

    assert isinstance(client, OpenAICompatClient)
    assert client._reasoning_effort == "balanced"


def test_get_llm_falls_back_to_profile_default_for_unknown_effort(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile(
        {
            **_profile("default", "https://example.test/v1", "gpt-test"),
            "reasoning_efforts": ["balanced", "provider-max"],
        }
    )
    store.set_default_llm_profile("default")

    client = get_llm(reasoning_effort="foreign-model-value")

    assert isinstance(client, OpenAICompatClient)
    assert client._reasoning_effort == "provider-max"


# --- from test_mock.py ---
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




async def test_no_user_message_returns_default() -> None:
    resp = await MockLLMClient().complete([{"role": "system", "content": "be helpful"}])
    assert resp.content
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






async def test_run_prompt_without_tools_returns_no_tool_calls() -> None:
    """When no tools are advertised the mock must not emit bash tool
    calls even for run: prompts (it returns the default reply)."""
    resp = await MockLLMClient().complete([_user("run: echo 2")], tools=None)
    assert resp.tool_calls == []
    assert resp.content == _DEFAULT_REPLY




async def test_run_prompt_with_non_bash_tools_returns_no_tool_calls() -> None:
    """Tools that do not include bash must not trigger a bash tool call."""
    resp = await MockLLMClient().complete(
        [_user("run: echo 2")],
        tools=[{"type": "function", "function": {"name": "search"}}],
    )
    assert resp.tool_calls == []
    assert resp.content == _DEFAULT_REPLY


_RECALL_TOOLS = [{"type": "function", "function": {"name": "memory"}}]


async def test_vendor_deadline_triggers_recall_tool_call() -> None:
    resp = await MockLLMClient().complete(
        [_user("What is the Q3 vendor onboarding deadline?")],
        tools=_RECALL_TOOLS,
    )
    assert resp.has_tool_calls
    assert resp.tool_calls[0].name == "memory"
    assert "vendor" in resp.tool_calls[0].arguments["query"].lower() or (
        "deadline" in resp.tool_calls[0].arguments["query"].lower()
    )


async def test_recall_keyword_triggers_recall() -> None:
    resp = await MockLLMClient().complete(
        [_user("recall support hours")], tools=_RECALL_TOOLS
    )
    assert resp.tool_calls[0].name == "memory"
    assert resp.tool_calls[0].arguments["query"] == "support hours"


