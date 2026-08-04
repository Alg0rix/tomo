"""Context assembly tests: history -> OpenAI messages + system prompt.

Covers :func:`coordinator_system_prompt` (file read + fallbacks),
:func:`history_to_messages` (role mapping, tool_call/tool_output grouping
and order-based pairing, skipping of internal entry types), and
:func:`build_messages` (full system + history + user assembly).

History fixtures are persisted via SQLite (``append_session_history`` +
``get_session_history``) so the transform is unit-tested against real store
shapes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.runtime.agent.context import (
    _FALLBACK_PROMPT,
    build_messages,
    coordinator_system_prompt,
    history_to_messages,
)
from app.services import store


def _hist(tmp_path: Path, *entries: dict[str, Any], db_name: str = "ctx.db") -> list[dict[str, Any]]:
    """Persist entries in a fresh SQLite session and return store history."""
    store.rebind(tmp_path / db_name)
    sid = store.create_swarm_session(["main"], user_id="web")
    for entry in entries:
        store.append_session_history(sid, entry)
    return store.get_session_history(sid)


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


def test_build_system_prompt_always_includes_current_time(tmp_path: Path) -> None:
    from app.runtime.agent.context import build_system_prompt, inject_current_time

    text = build_system_prompt(None, home_root=tmp_path)
    assert "## Current time" in text
    assert "UTC:" in text
    assert "Local:" in text
    assert "date" in text.lower()  # points agents at bash for live clock
    # Idempotent: second inject does not stack sections
    twice = inject_current_time(inject_current_time("You are Tomo."))
    assert twice.count("## Current time") == 1


def test_prompt_clock_freeze_is_stable_within_turn(tmp_path: Path) -> None:
    from app.runtime.agent.context import (
        freeze_prompt_clock,
        inject_current_time,
        reset_prompt_clock,
    )

    tok = freeze_prompt_clock()
    try:
        a = inject_current_time("base")
        b = inject_current_time("base")
        assert a == b  # once per turn — not re-stamped mid-turn
        assert a.count("## Current time") == 1
        assert "bash" in a.lower() or "`date`" in a
    finally:
        reset_prompt_clock(tok)


def test_build_messages_stamps_time_on_custom_system_prompt() -> None:
    msgs = build_messages([], user_message="hi", system_prompt="Custom agent.")
    assert msgs[0]["role"] == "system"
    assert "## Current time" in msgs[0]["content"]
    assert "Custom agent." in msgs[0]["content"]


# --- history_to_messages ------------------------------------------------


def test_history_to_messages_empty_inputs() -> None:
    assert history_to_messages(None) == []
    assert history_to_messages([]) == []


def test_user_and_final_map_to_chat_roles(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "user", "content": "hi"},
        {"type": "final", "content": "hello"},
    )
    assert history_to_messages(history) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_tool_call_and_output_are_paired(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 2"}},
        {"type": "tool_output", "content": "4"},
    )
    msgs = history_to_messages(history)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["content"] is None
    call = msgs[0]["tool_calls"][0]
    assert call["id"] == "hist_call_0"
    assert call["type"] == "function"
    assert call["function"]["name"] == "bash"
    assert json.loads(call["function"]["arguments"]) == {"command": "echo 2"}
    assert msgs[1] == {"role": "tool", "tool_call_id": "hist_call_0", "content": "4"}


def test_consecutive_tool_calls_grouped_then_outputs_paired_in_order(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 1"}},
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 2"}},
        {"type": "tool_output", "content": "2"},
        {"type": "tool_output", "content": "4"},
    )
    msgs = history_to_messages(history)
    # One assistant message carrying both calls, then two tool messages.
    assert len(msgs) == 3
    assert msgs[0]["role"] == "assistant"
    assert [c["id"] for c in msgs[0]["tool_calls"]] == ["hist_call_0", "hist_call_1"]
    assert [m["tool_call_id"] for m in msgs[1:]] == ["hist_call_0", "hist_call_1"]
    assert [m["content"] for m in msgs[1:]] == ["2", "4"]


def test_internal_entry_types_are_skipped(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "thinking", "content": "hmm"},
        {"type": "user", "content": "hi"},
        {"type": "intermediate", "content": "x"},
        {"type": "delegate", "content": "handoff"},
        {"type": "final", "content": "yo"},
        {"type": "error", "content": "boom"},
    )
    # Without for_agent_id, delegate stays internal (legacy single-agent).
    assert history_to_messages(history) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]


def test_foreign_agent_final_attributed_for_coordinator(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "user", "content": "ping google"},
        {
            "type": "delegate",
            "content": "Handing off to Ops",
            "agent_id": "ops",
            "to": "ops",
        },
        {
            "type": "final",
            "content": "Avg RTT 15ms from aio-serv.",
            "agent_id": "ops",
        },
        db_name="ctx_swarm.db",
    )
    msgs = history_to_messages(history, for_agent_id="main")
    assert msgs[0] == {"role": "user", "content": "ping google"}
    assert msgs[1]["role"] == "assistant"
    assert "[Swarm]" in msgs[1]["content"]
    assert msgs[2]["role"] == "assistant"
    assert "[From Ops" in msgs[2]["content"]
    assert "15ms" in msgs[2]["content"]
    # Must not look like an unattributed Tomo answer.
    assert msgs[2]["content"] != "Avg RTT 15ms from aio-serv."


def test_delegate_note_skipped_for_target_agent(tmp_path: Path) -> None:
    """Ops must not see ``[Swarm] Handing off to Ops`` as its own prior turn."""
    history = _hist(
        tmp_path,
        {"type": "user", "content": "@ops check hostname"},
        {
            "type": "delegate",
            "content": "Handing off to Ops",
            "agent_id": "ops",
            "from": "main",
            "to": "ops",
        },
        db_name="ctx_swarm_target.db",
    )
    msgs = history_to_messages(history, for_agent_id="ops")
    assert msgs == [{"role": "user", "content": "@ops check hostname"}]
    assert not any("[Swarm]" in (m.get("content") or "") for m in msgs)

def test_foreign_tools_folded_not_as_self_tool_calls(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "user", "content": "compare latency"},
        {
            "type": "tool_call",
            "function": "bash",
            "params": {"command": "ping -c 1 google.com"},
            "agent_id": "ops",
        },
        {
            "type": "tool_output",
            "content": "rtt avg 15.5 ms",
            "agent_id": "ops",
        },
        {
            "type": "final",
            "content": "local_dev is faster",
            "agent_id": "ops",
        },
        db_name="ctx_fold.db",
    )
    msgs = history_to_messages(history, for_agent_id="main")
    # No OpenAI tool_calls for Ops tools — coordinator must not think it ran them.
    assert not any(m.get("tool_calls") for m in msgs)
    fold = next(m for m in msgs if m["role"] == "assistant" and "tool run" in (m.get("content") or ""))
    assert "ping" in fold["content"]
    assert "15.5" in fold["content"]
    final = next(
        m
        for m in msgs
        if m["role"] == "assistant" and "local_dev is faster" in (m.get("content") or "")
    )
    assert "[From Ops" in final["content"]


def test_unknown_entry_type_is_skipped_safely(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "user", "content": "hi"},
        {"type": "mystery", "content": "???"},
        {"type": "final", "content": "yo"},
    )
    assert history_to_messages(history) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]


def test_separate_turns_pair_with_fresh_ids(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "user", "content": "run: echo 1"},
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 1"}},
        {"type": "tool_output", "content": "2"},
        {"type": "final", "content": "done"},
        {"type": "user", "content": "run: echo 2"},
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 2"}},
        {"type": "tool_output", "content": "4"},
    )
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


def test_missing_params_default_to_empty_object(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "tool_call", "function": "bash"},
        {"type": "tool_output", "content": "ok"},
    )
    msgs = history_to_messages(history)
    assert json.loads(msgs[0]["tool_calls"][0]["function"]["arguments"]) == {}


# --- build_messages -----------------------------------------------------


def test_build_messages_assembles_system_history_user(tmp_path: Path) -> None:
    history = _hist(tmp_path, {"type": "user", "content": "earlier"}, db_name="build1.db")
    msgs = build_messages(history, "now", system_prompt="be brief")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"].startswith("be brief")
    assert "## Current time" in msgs[0]["content"]
    assert msgs[1:] == [
        {"role": "user", "content": "earlier"},
        {"role": "user", "content": "now"},
    ]


def test_build_messages_omits_user_message_when_none(tmp_path: Path) -> None:
    history = _hist(tmp_path, {"type": "user", "content": "earlier"}, db_name="build2.db")
    msgs = build_messages(history, None, system_prompt="s")
    assert [m["role"] for m in msgs] == ["system", "user"]


def test_build_messages_defaults_system_prompt() -> None:
    msgs = build_messages(None, "hi")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"]  # non-empty default from defaults file
    assert msgs[-1] == {"role": "user", "content": "hi"}


# --- unpaired / surplus tool pairing -----------------------------------


def test_unpaired_tool_call_gets_synthetic_tool_result(tmp_path: Path) -> None:
    """A tool_call with no following tool_output still gets a tool result."""
    history = _hist(
        tmp_path,
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 2"}},
        {"type": "user", "content": "are you done?"},
        db_name="unpaired.db",
    )
    msgs = history_to_messages(history)
    # assistant tool_calls, synthetic tool result, then the later user.
    assert [m["role"] for m in msgs] == ["assistant", "tool", "user"]
    assert msgs[0]["tool_calls"][0]["id"] == msgs[1]["tool_call_id"]
    assert msgs[1]["content"] == "Error: missing tool result"
    assert msgs[2] == {"role": "user", "content": "are you done?"}


def test_multiple_unpaired_calls_each_get_synthetic_result(tmp_path: Path) -> None:
    history = _hist(
        tmp_path,
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 1"}},
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 2"}},
        {"type": "user", "content": "hello?"},
        db_name="multi_unpaired.db",
    )
    msgs = history_to_messages(history)
    assert [m["role"] for m in msgs] == ["assistant", "tool", "tool", "user"]
    ids = [c["id"] for c in msgs[0]["tool_calls"]]
    assert [m["tool_call_id"] for m in msgs[1:3]] == ids
    assert all(m["content"] == "Error: missing tool result" for m in msgs[1:3])


def test_partial_outputs_pair_first_calls_then_synthesize_the_rest(tmp_path: Path) -> None:
    """Fewer outputs than calls: pair in order, synthesize the remainder."""
    history = _hist(
        tmp_path,
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 1"}},
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 2"}},
        {"type": "tool_output", "content": "2"},
        db_name="partial.db",
    )
    msgs = history_to_messages(history)
    assert [m["role"] for m in msgs] == ["assistant", "tool", "tool"]
    ids = [c["id"] for c in msgs[0]["tool_calls"]]
    assert msgs[1] == {"role": "tool", "tool_call_id": ids[0], "content": "2"}
    assert msgs[2] == {
        "role": "tool",
        "tool_call_id": ids[1],
        "content": "Error: missing tool result",
    }


def test_surplus_tool_outputs_are_dropped_not_reused(tmp_path: Path) -> None:
    """Extra tool_output rows beyond the calls must not map onto the last id."""
    history = _hist(
        tmp_path,
        {"type": "tool_call", "function": "bash", "params": {"command": "echo 2"}},
        {"type": "tool_output", "content": "4"},
        {"type": "tool_output", "content": "stray"},
        db_name="surplus.db",
    )
    msgs = history_to_messages(history)
    # One assistant call, one paired tool result; the stray output is dropped.
    assert [m["role"] for m in msgs] == ["assistant", "tool"]
    assert msgs[1] == {"role": "tool", "tool_call_id": "hist_call_0", "content": "4"}
