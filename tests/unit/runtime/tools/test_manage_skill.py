"""manage_skill + learning eligibility tests."""

from __future__ import annotations

import pytest

from app.runtime.agent.learning import (
    compact_tool_trail,
    decide_review,
    is_learning_eligible,
    observe_turn,
    reset_learning_cooldowns,
    snapshot,
)
from app.runtime.agent.metrics import TurnMetrics
from app.runtime.tools.registry import execute, reset_registry
from app.services import store


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch) -> None:
    reset_registry()
    reset_learning_cooldowns()
    store.rebind(tmp_path / "learn.db")
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "home")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    # Deterministic nudges for tests
    store.update_settings(
        {
            "learning_enabled": True,
            "learning_memory_nudge_turns": 3,
            "learning_skill_nudge_iters": 3,
            "learning_cooldown_sec": 0,
        }
    )
    yield
    reset_registry()
    reset_learning_cooldowns()


def test_manage_skill_create_and_use() -> None:
    out = execute(
        "manage_skill",
        {
            "action": "create",
            "skill_id": "python-unit-testing",
            "description": "How to add and run pytest unit tests",
            "body": (
                "## Steps\n"
                "1. Write tests under tests/\n"
                "2. Run `uv run pytest path -q`\n"
                "3. Fix failures before claiming done\n"
            ),
        },
    )
    assert out.startswith("Created skill"), out
    listed = execute("list_skills", {})
    assert "python-unit-testing" in listed
    body = execute("use_skill", {"skill_id": "python-unit-testing"})
    assert "uv run pytest" in body


def test_manage_skill_patch() -> None:
    execute(
        "manage_skill",
        {
            "action": "create",
            "skill_id": "patch-me",
            "description": "Patch demo",
            "body": "Use foo.\n",
        },
    )
    out = execute(
        "manage_skill",
        {
            "action": "patch",
            "skill_id": "patch-me",
            "old_string": "Use foo.",
            "new_string": "Use bar.",
        },
    )
    assert "Patched" in out
    body = execute("use_skill", {"skill_id": "patch-me"})
    assert "Use bar." in body


def test_manage_skill_rejects_duplicate_without_overwrite() -> None:
    args = {
        "action": "create",
        "skill_id": "dup",
        "description": "d",
        "body": "body",
    }
    assert execute("manage_skill", args).startswith("Created")
    assert execute("manage_skill", args).startswith("Error")


def test_learning_eligibility_uses_counters_not_keywords() -> None:
    # Chat-only turn: no skill review; memory waits for nudge interval.
    m = TurnMetrics(agent_id="a1", ended_kind="final", tool_calls=0)
    flags = decide_review(metrics=m)
    assert flags["review_skills"] is False
    assert flags["review_memory"] is False  # turn 1 of 3

    flags = decide_review(metrics=m)
    assert flags["review_memory"] is False  # turn 2 of 3

    flags = decide_review(metrics=m)
    assert flags["review_memory"] is True  # turn 3 → memory nudge

    # Tool-heavy turn → skill nudge immediately (independent of memory counter).
    reset_learning_cooldowns()
    m2 = TurnMetrics(agent_id="a2", ended_kind="final", tool_calls=3)
    flags = decide_review(metrics=m2)
    assert flags["review_skills"] is True

    # Skill touched → skill refine even with few tools.
    reset_learning_cooldowns()
    m3 = TurnMetrics(agent_id="a3", ended_kind="final", tool_calls=1)
    flags = decide_review(metrics=m3, skills_touched=["python-unit-testing"])
    assert flags["review_skills"] is True

    # Nested never reviews.
    assert decide_review(metrics=m3, nested=True) == {
        "review_memory": False,
        "review_skills": False,
    }

    # Errors never review.
    m3.ended_kind = "error"
    assert not is_learning_eligible(metrics=m3, skills_touched=["x"])


def test_skill_iters_accumulate_across_turns() -> None:
    reset_learning_cooldowns()
    m = TurnMetrics(agent_id="cumul", ended_kind="final", tool_calls=2)
    assert decide_review(metrics=m)["review_skills"] is False  # 2 < 3
    assert decide_review(metrics=m)["review_skills"] is True  # 2+2 >= 3? wait 2+2=4


def test_sticky_due_survives_inflight() -> None:
    """If a review is in-flight, dues stay armed for the next opportunity."""
    from app.runtime.agent.learning.state import begin_review, finish_review

    reset_learning_cooldowns()
    plan = None
    for _ in range(3):
        plan = observe_turn(
            agent_id="sticky",
            tool_calls=0,
            ended_kind="final",
        )
    assert plan is not None and plan.review_memory
    assert begin_review(plan) is True
    # While in-flight, another observe should not return a new plan
    blocked = observe_turn(agent_id="sticky", tool_calls=5, ended_kind="final")
    assert blocked is None
    finish_review("sticky", saved=False)
    # Memory was consumed by begin_review; skill iters from the blocked turn
    # should still arm skill review.
    nxt = observe_turn(agent_id="sticky", tool_calls=0, ended_kind="final")
    # skill iters were advanced during blocked observe (5 tools)
    assert nxt is not None
    assert nxt.review_skills is True


def test_correction_keywords_do_not_gate() -> None:
    """English cues are prompt guidance only — counters decide eligibility."""
    reset_learning_cooldowns()
    m = TurnMetrics(agent_id="cue", ended_kind="final", tool_calls=0)
    # Chat-only turn 1 must NOT force a review (counters only).
    assert not is_learning_eligible(metrics=m)


def test_compact_tool_trail() -> None:
    msgs = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
                }
            ],
        },
        {"role": "tool", "name": "bash", "content": "ok\nline2"},
    ]
    trail = compact_tool_trail(msgs)
    assert "bash" in trail
    assert "✓" in trail
    assert "tools=" in trail


def test_snapshot_exposes_counters() -> None:
    reset_learning_cooldowns()
    observe_turn(agent_id="snap", tool_calls=1, ended_kind="final")
    snap = snapshot("snap")
    assert snap["turns_since_memory"] == 1
    assert snap["iters_since_skill"] == 1
