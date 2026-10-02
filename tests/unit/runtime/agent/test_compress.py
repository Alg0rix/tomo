from copy import deepcopy

import pytest

from app.runtime.agent.compress import (
    ContextBudgetError, estimate_prompt_tokens, maybe_compress_messages, prompt_budget,
)


def call_and_results(size=1000):
    return [
        {"role": "assistant", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "A" * size},
        {"role": "tool", "tool_call_id": "b", "content": "B" * size},
    ]


def assert_paired(messages):
    pending = set()
    for message in messages:
        if message.get("tool_calls"):
            assert not pending
            pending.update(call["id"] for call in message["tool_calls"])
        elif message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
        else:
            assert not pending
    assert not pending


@pytest.mark.parametrize("count", [1, 5, 12, 30])
def test_large_history_fits_and_keeps_latest_request(count):
    messages = [{"role": "system", "content": "Required instructions."}]
    messages += [{"role": "user", "content": "Old question " + "X" * 80_000} for _ in range(count)]
    latest = {"role": "user", "content": "[SYSTEM] This is my actual new request."}
    messages.append(latest)
    snapshot = deepcopy(messages)
    result = maybe_compress_messages(messages, context_window=128_000)
    if count >= 12:
        assert result is not messages
    assert latest in result
    assert result[0] == messages[0]
    assert estimate_prompt_tokens(result) <= prompt_budget(128_000)
    assert messages == snapshot


def test_two_message_500k_history_is_not_exempt():
    messages = [{"role": "user", "content": "X" * 2_000_000},
                {"role": "user", "content": "Continue."}]
    result = maybe_compress_messages(messages, context_window=128_000)
    assert result is not messages
    assert result[-1] == messages[-1]
    assert estimate_prompt_tokens(result) <= prompt_budget(128_000)


def test_large_recent_tool_results_are_trimmed_without_breaking_calls():
    messages = [{"role": "system", "content": "Instructions"},
                {"role": "user", "content": "Inspect these files"}, *call_and_results(1_000_000)]
    snapshot = deepcopy(messages)
    result = maybe_compress_messages(messages, context_window=128_000)
    assert_paired(result)
    assert any(message["role"] == "tool" for message in result)
    assert estimate_prompt_tokens(result) <= prompt_budget(128_000)
    assert messages == snapshot


def test_old_tool_exchanges_are_removed_as_a_whole():
    messages = [{"role": "user", "content": "Old request"}, *call_and_results(100_000),
                {"role": "system", "content": "Live instructions must survive."},
                {"role": "user", "content": "New request"}]
    result = maybe_compress_messages(messages, context_window=4096, keep_recent=2)
    assert_paired(result)
    assert messages[-2] in result
    assert messages[-1] in result
    assert estimate_prompt_tokens(result) <= prompt_budget(4096)


@pytest.mark.parametrize("role", ["user", "system", "developer"])
def test_required_oversized_input_is_rejected(role):
    messages = [{"role": role, "content": "X" * 2_000_000}]
    with pytest.raises(ContextBudgetError, match="latest request exceed"):
        maybe_compress_messages(messages, context_window=128_000)


def test_tools_and_reply_reserve_are_included():
    tools = [{"type": "function", "function": {"name": "tool", "description": "X" * 30_000}}]
    messages = [{"role": "system", "content": "Instructions"},
                {"role": "user", "content": "Old request " + "Y" * 90_000},
                {"role": "user", "content": "Latest request"}]
    result = maybe_compress_messages(messages, context_window=32_000, tools=tools)
    assert estimate_prompt_tokens(result, tools) <= prompt_budget(32_000)
    with pytest.raises(ContextBudgetError):
        maybe_compress_messages(messages, context_window=4096, tools=tools)


def test_large_model_keeps_history_above_24k():
    messages = [{"role": "user", "content": "X" * 200_000}]
    assert maybe_compress_messages(messages, context_window=1_000_000) is messages


def test_image_base64_is_not_counted_as_prompt_text():
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Explain this image."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 2_000_000}},
    ]}]
    assert estimate_prompt_tokens(messages) < prompt_budget(128_000)
    assert maybe_compress_messages(messages, context_window=128_000) is messages


def test_user_request_with_summary_prefix_is_still_protected():
    messages = [{"role": "user", "content": "[SYSTEM] Earlier conversation was compressed " + "X" * 2_000_000}]
    with pytest.raises(ContextBudgetError):
        maybe_compress_messages(messages, context_window=128_000)


def test_repeated_compaction_is_bounded():
    messages = [{"role": "user", "content": f"Fact {index}: " + "X" * 1000} for index in range(5000)]
    messages.append({"role": "user", "content": "Latest request"})
    result = maybe_compress_messages(messages, context_window=32_000)
    assert estimate_prompt_tokens(result) <= prompt_budget(32_000)
    result = maybe_compress_messages(result, context_window=4096)
    assert estimate_prompt_tokens(result) <= prompt_budget(4096)
    assert result[-1] == messages[-1]
