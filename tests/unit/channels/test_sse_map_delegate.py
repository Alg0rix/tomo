"""delegate_call_id is forwarded on swarm SSE + history entries."""

from __future__ import annotations

import json

from app.channels.sse_map import map_loop_event


def _parse(chunks: list[str]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for chunk in chunks:
        name = ""
        data: dict = {}
        for line in chunk.strip().split("\n"):
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
        out.append((name, data))
    return out


def test_delegate_events_carry_delegate_call_id() -> None:
    chunks, entries, seq = map_loop_event(
        {
            "kind": "delegate",
            "from": "main",
            "to": "ops",
            "reason": "check disk",
            "task": "check disk",
            "to_name": "Ops",
            "parallel_index": 1,
            "parallel_total": 2,
            "delegate_call_id": "call_ops_a",
        },
        "ops",
        "Ops",
        0,
        "turn-1",
    )
    assert seq == 1
    events = _parse(chunks)
    assert events[0][0] == "delegate"
    assert events[0][1]["delegate_call_id"] == "call_ops_a"
    assert entries[0]["params"]["delegate_call_id"] == "call_ops_a"


def test_nested_thinking_stamps_delegate_call_id() -> None:
    chunks, entries, _ = map_loop_event(
        {
            "kind": "thinking",
            "content": "looking…",
            "delegate_call_id": "call_ops_b",
        },
        "ops",
        "Ops",
        0,
        "turn-1",
    )
    events = _parse(chunks)
    assert events[0][1]["delegate_call_id"] == "call_ops_b"
    assert entries[0]["delegate_call_id"] == "call_ops_b"


def test_tool_keeps_own_call_id_and_delegate_call_id() -> None:
    chunks, entries, _ = map_loop_event(
        {
            "kind": "tool",
            "tool": "bash",
            "args": {"command": "uptime"},
            "call_id": "call_bash_1",
            "delegate_call_id": "call_ops_a",
        },
        "ops",
        "Ops",
        0,
        "turn-1",
    )
    events = _parse(chunks)
    assert events[0][1]["call_id"] == "call_bash_1"
    assert events[0][1]["delegate_call_id"] == "call_ops_a"
    assert entries[0]["call_id"] == "call_bash_1"
    assert entries[0]["delegate_call_id"] == "call_ops_a"
