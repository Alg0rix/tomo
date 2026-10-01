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


def test_usage_counts_foreground_not_pagination_review_or_history_replay() -> None:
    from app.runtime.agent.learning.state import enter_review_scope, exit_review_scope
    from app.services.chat import expand_slash_skill

    for sid in ("popular", "unused"):
        execute("manage_skill", {"action": "create", "skill_id": sid,
                "description": "Usage demo", "body": "Use pytest. " * 20})
    execute("use_skill", {"skill_id": "popular", "limit": 20})
    execute("use_skill", {"skill_id": "popular", "offset": 20})
    execute("use_skill", {"skill_id": "popular", "offset": 99999})
    token = enter_review_scope()
    try:
        execute("use_skill", {"skill_id": "popular"})
        assert execute("manage_skill", {"action": "delete", "skill_id": "popular"}).startswith("Error")
    finally:
        exit_review_scope(token)
    assert store.get_skill("popular")["use_count"] == 1
    session = store.create_home_session("web")
    sid = session.get("session_id") or session["id"]
    store.append_session_history(sid, {"type": "user", "content": "/popular run tests"})
    expand_slash_skill("/popular run tests")
    expand_slash_skill("/popular run tests")
    store.sync_skills()
    assert store.get_skill("popular")["use_count"] == 2
    assert store.get_skill("unused")["use_count"] == 0
    top = store.companion_snapshot()["most_used_skills"]
    assert top[0]["id"] == "popular"
    assert top[0]["use_count"] == 2
    assert top[0]["last_used_at"] > 0
    assert "unused" not in [s["id"] for s in top]


def test_merge_preserves_package_and_assignment_and_refuses_file_conflicts() -> None:
    from app.extensions.skills import read_skill_body, read_skill_file

    for sid, body in (("python-tests", "Run pytest."), ("python-fixtures", "Use fixtures.")):
        execute("manage_skill", {"action": "create", "skill_id": sid,
                "description": "Python testing", "body": body})
        execute("manage_skill", {"action": "write_file", "skill_id": sid,
                "file_path": "references/example.md", "content": body})
    execute("manage_skill", {"action": "write_file", "skill_id": "python-fixtures",
            "file_path": "scripts/check.py", "content": "print('fixture')"})
    agent = store.list_agents()[0]
    store.set_agent_skills(agent["id"], ["python-fixtures"])
    execute("use_skill", {"skill_id": "python-fixtures"})
    args = {"action": "merge", "skill_id": "python-fixtures", "target_skill_id": "python-tests",
            "body": "Run pytest. Use fixtures. See references/example.md and scripts/check.py."}
    assert "conflict" in execute("manage_skill", args)
    assert read_skill_body("python-tests") == "Run pytest."
    assert store.get_skill("python-fixtures")["enabled"]
    execute("manage_skill", {"action": "write_file", "skill_id": "python-tests",
            "file_path": "references/example.md", "content": "Use fixtures."})
    assert execute("manage_skill", args).startswith("Merged skill")
    store.sync_skills()
    source = store.get_skill("python-fixtures")
    assert not source["enabled"] and source["archived_at"] > 0
    assert source["merged_into"] == "python-tests" and source["use_count"] == 1
    assert read_skill_body("python-fixtures") == "Use fixtures."
    assert "Use fixtures" in read_skill_body("python-tests")
    assert "print('fixture')" in read_skill_file("python-tests", "scripts/check.py")
    assigned = [s["id"] for s in store.get_agent_skills(agent["id"]) if s["assigned"]]
    assert assigned == ["python-tests"]
    assert store.get_agent(agent["id"])["skill_count"] == 1
    assert "is archived" in execute("use_skill", {"skill_id": "python-fixtures"})
    assert execute("list_skills", {"query": "python-fixtures"}).startswith("No skills matched")
    restored = store.update_skill("python-fixtures", {"enabled": True})
    assert restored["enabled"] and restored["archived_at"] == 0
    assert restored["use_count"] == 1


def test_stale_archive_respects_age_assignments_ownership_and_restore(monkeypatch) -> None:
    import time
    from app.services.chat import resolve_slash_skill

    execute("manage_skill", {"action": "create", "skill_id": "old-local",
            "description": "Old local", "body": "Old procedure."})
    execute("manage_skill", {"action": "create", "skill_id": "assigned-local",
            "description": "Assigned local", "body": "Protected procedure."})
    agent = store.list_agents()[0]
    store.set_agent_skills(agent["id"], ["assigned-local"])
    execute("use_skill", {"skill_id": "old-local"})
    future = time.time() + 91 * 86400
    monkeypatch.setattr(time, "time", lambda: future)
    execute("manage_skill", {"action": "create", "skill_id": "new-local",
            "description": "New local", "body": "Recent procedure."})
    assert store.maintain_skills() == ["old-local"]
    assert store.get_skill("old-local")["use_count"] == 1
    assert store.get_skill("assigned-local")["enabled"]
    assert store.get_skill("new-local")["enabled"]
    assert all(s["enabled"] for s in store.list_skills() if s["source"] == "internal")
    assert resolve_slash_skill("/old-local") is None
    store.update_skill("old-local", {"enabled": True})
    assert store.maintain_skills() == []
    assert resolve_slash_skill("/old-local") is not None
    internal = next(s for s in store.list_skills() if s["source"] == "internal")
    assert execute("manage_skill", {"action": "merge", "skill_id": internal["id"],
                   "target_skill_id": "new-local", "body": "Merged."}).startswith("Error")
