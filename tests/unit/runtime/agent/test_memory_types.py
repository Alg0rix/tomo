"""Learning OS memory types, evaluator, and saved classification."""

from __future__ import annotations

from app.runtime.agent.learning.evaluator import evaluate_write
from app.runtime.agent.learning.memory_types import (
    MEMORY_TYPES,
    classify_actions,
    classify_review_action,
    is_successful_write,
    memory_type_for_tool,
    store_hint,
)


def test_eight_memory_types() -> None:
    assert len(MEMORY_TYPES) == 8
    assert "episodic" in MEMORY_TYPES
    assert "semantic" in MEMORY_TYPES
    assert "shared" in MEMORY_TYPES
    for t in MEMORY_TYPES:
        assert store_hint(t)


def test_memory_type_for_tool_targets() -> None:
    assert memory_type_for_tool("remember") == "semantic"
    assert memory_type_for_tool("memory", arguments={"target": "user"}) == "user"
    assert memory_type_for_tool("memory", arguments={"target": "project"}) == "project"
    assert memory_type_for_tool("memory", arguments={"target": "memory"}) == "agent"
    assert memory_type_for_tool("save_artifact") == "execution"
    assert memory_type_for_tool("list_skills") == "agent"


def test_saved_only_on_successful_writes() -> None:
    assert is_successful_write("memory", "added to USER.md (120 chars, 3 entries).")
    assert not is_successful_write("list_skills", "skill-a, skill-b")
    assert not is_successful_write("memory", "Error: content is empty")
    assert not is_successful_write(
        "memory", "near-duplicate already present (3 entries)."
    )
    assert not is_successful_write("memory", "already present (2 entries).")


def test_soft_evaluator_near_duplicate() -> None:
    ev = evaluate_write("memory", "near-duplicate already present")
    assert ev["ok"] is False
    assert ev["reason"] == "near_duplicate"
    assert ev["saved_eligible"] is False


def test_classify_actions_saved_flag() -> None:
    items = [
        classify_review_action(
            "list_skills", result_text="python-unit-testing"
        ),
        classify_review_action(
            "memory",
            arguments={"target": "user", "action": "add"},
            result_text="added to USER.md (80 chars, 1 entries).",
        ),
    ]
    agg = classify_actions([], classified=items)
    assert agg["saved"] is True
    assert "user" in agg["memory_types"]
    assert len(agg["reads"]) == 1
    assert len(agg["writes"]) == 1
    assert agg["confidence"] >= 0.9


def test_classify_read_only_not_saved() -> None:
    items = [
        classify_review_action("list_skills", result_text="a,b"),
        classify_review_action("use_skill", result_text="# body"),
    ]
    agg = classify_actions([], classified=items)
    assert agg["saved"] is False
    assert agg["confidence"] == 0.0
    assert all(i["kind"] == "read" for i in items)
