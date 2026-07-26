"""Context assembly tests: history -> OpenAI messages + system prompt.

Covers :func:`coordinator_system_prompt` (file read + fallbacks),
:func:`history_to_messages` (role mapping, tool_call/tool_output grouping
and order-based pairing, skipping of internal entry types), and
:func:`build_messages` (full system + history + user assembly).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.runtime.agent.context import (
    _FALLBACK_PROMPT,
    build_messages,
    coordinator_system_prompt,
    history_to_messages,
)


# --- system prompt ------------------------------------------------------


def test_system_prompt_reads_repo_defaults_file() -> None:
    prompt = coordinator_system_prompt()
    assert "Tomo" in prompt
    # Whitespace is stripped so the model never sees a leading newline.
    assert prompt == prompt.strip()


def test_system_prompt_falls_back_when_file_missing(tmp_path: Path) -> None:
    assert coordinator_system_prompt(path=tmp_path / "nope.md") == _FALLBACK_PROMPT


def test_system_prompt_falls_back_on_blank_file(tmp_path: Path) -> None:
    blank = tmp_path / "blank.md"
    blank.write_text("   \n  \n", encoding="utf-8")
    assert coordinator_system_prompt(path=blank) == _FALLBACK_PROMPT


def test_system_prompt_uses_file_contents(tmp_path: Path) -> None:
    f = tmp_path / "sys.md"
    f.write_text("You are a test agent.\n", encoding="utf-8")
    assert coordinator_system_prompt(path=f) == "You are a test agent."


# --- history_to_messages ------------------------------------------------


def test_history_to_messages_empty_inputs() -> None:
    assert history_to_messages(None) == []
    assert history_to_messages([]) == []


def test_user_and_final_map_to_chat_roles() -> None:
    history = [
        {"type": "user", "content": "hi"},
        {"type": "final", "content": "hello"},
    ]
    assert history_to_messages(history) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_tool_call_and_output_are_paired() -> None:
    history = [
        {"type": "tool_call", "function": "calculator", "params": {"expression": "2 + 2"}},
        {"type": "tool_output", "content": "4"},
    ]
    msgs = history_to_messages(history)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] is None
    call = msgs[0]["tool_calls"][0]
    assert call["id"] == "hist_call_0"
    assert call["type"] == "function"
    assert call["function"]["name"] == "calculator"
    assert json.loads(call["function"]["arguments"]) == {"expression": "2 + 2"}
    assert msgs[1] == {"role": "tool", "tool_call_id": "hist_call_0", "content": "4"}


def test_consecutive_tool_calls_grouped_then_outputs_paired_in_order() -> None:
    history = [
        {"type": "tool_call", "function": "calculator", "params": {"expression": "1 + 1"}},
        {"type": "tool_call", "function": "calculator", "params": {"expression": "2 + 2"}},
        {"type": "tool_output", "content": "2"},
        {"type": "tool_output", "content": "4"},
    ]
    msgs = history_to_messages(history)
    # One assistant message carrying both calls, then two tool messages.
    assert len(msgs) == 3
    assert msgs[0]["role"] == "assistant"
    assert [c["id"] for c in msgs[0]["tool_calls"]] == ["hist_call_0", "hist_call_1"]
    assert [m["tool_call_id"] for m in msgs[1:]] == ["hist_call_0", "hist_call_1"]
    assert [m["content"] for m in msgs[1:]] == ["2", "4"]


def test_internal_entry_types_are_skipped() -> None:
    history = [
        {"type": "thinking", "content": "hmm"},
        {"type": "user", "content": "hi"},
        {"type": "intermediate", "content": "x"},
        {"type": "delegate", "content": "handoff"},
        {"type": "final", "content": "yo"},
        {"type": "error", "content": "boom"},
    ]
    assert history_to_messages(history) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]


def test_unknown_entry_type_is_skipped_safely() -> None:
    history = [
        {"type": "user", "content": "hi"},
        {"type": "mystery", "content": "???"},
        {"type": "final", "content": "yo"},
    ]
    assert history_to_messages(history) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]


def test_separate_turns_pair_with_fresh_ids() -> None:
    history = [
        {"type": "user", "content": "calculate 1 + 1"},
        {"type": "tool_call", "function": "calculator", "params": {"expression": "1 + 1"}},
        {"type": "tool_output", "content": "2"},
        {"type": "final", "content": "done"},
        {"type": "user", "content": "calculate 2 + 2"},
        {"type": "tool_call", "function": "calculator", "params": {"expression": "2 + 2"}},
        {"type": "tool_output", "content": "4"},
    ]
    msgs = history_to_messages(history)
    assert [m["role"] for m in msgs] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
        "assistant",
        "tool",
    ]
    # Second turn reuses neither id nor pairing from the first turn.
    assert msgs[5]["tool_calls"][0]["id"] == "hist_call_1"
    assert msgs[6]["tool_call_id"] == "hist_call_1"


def test_missing_params_default_to_empty_object() -> None:
    history = [
        {"type": "tool_call", "function": "calculator"},
        {"type": "tool_output", "content": "ok"},
    ]
    msgs = history_to_messages(history)
    assert json.loads(msgs[0]["tool_calls"][0]["function"]["arguments"]) == {}


# --- build_messages -----------------------------------------------------


def test_build_messages_assembles_system_history_user() -> None:
    history = [{"type": "user", "content": "earlier"}]
    msgs = build_messages(history, "now", system_prompt="be brief")
    assert msgs == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "earlier"},
        {"role": "user", "content": "now"},
    ]


def test_build_messages_omits_user_message_when_none() -> None:
    history = [{"type": "user", "content": "earlier"}]
    msgs = build_messages(history, None, system_prompt="s")
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_build_messages_defaults_system_prompt() -> None:
    msgs = build_messages(None, "hi")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"]  # non-empty default from defaults file
    assert msgs[-1] == {"role": "user", "content": "hi"}
