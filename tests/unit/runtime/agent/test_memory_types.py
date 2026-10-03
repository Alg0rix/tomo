"""Learning OS memory types, evaluator, and saved classification."""

from __future__ import annotations

from app.runtime.agent.learning.memory_types import (
    MEMORY_TYPES,
    is_successful_write,
    memory_type_for_tool,
    store_hint,
)


def test_nine_memory_types() -> None:
    assert len(MEMORY_TYPES) == 9
    assert "diary" in MEMORY_TYPES
    assert "episodic" in MEMORY_TYPES
    assert "semantic" in MEMORY_TYPES
    assert "shared" in MEMORY_TYPES
    for t in MEMORY_TYPES:
        assert store_hint(t)


def test_memory_type_for_tool_targets() -> None:
    assert memory_type_for_tool("memory", arguments={"entity": "topic/general"}) == "semantic"
    assert memory_type_for_tool("memory", arguments={"entity": "user/profile"}) == "user"
    assert memory_type_for_tool("memory", arguments={"entity": "project/tomo"}) == "project"
    assert memory_type_for_tool("memory", arguments={"entity": "agent/ops"}) == "agent"
    assert memory_type_for_tool("save_artifact") == "execution"
    assert memory_type_for_tool("record_episode") == "episodic"
    assert memory_type_for_tool("recall_episodes") == "episodic"
    assert memory_type_for_tool("list_skills") == "agent"


def test_saved_only_on_successful_writes() -> None:
    assert is_successful_write("memory", "Saved vault fact.")
    assert not is_successful_write("list_skills", "skill-a, skill-b")
    assert not is_successful_write("memory", "Error: content is empty")
    assert not is_successful_write(
        "memory", "near-duplicate already present (3 entries)."
    )
    assert not is_successful_write("memory", "already present (2 entries).")






