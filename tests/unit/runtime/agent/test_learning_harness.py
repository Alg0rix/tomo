"""Learning harness — digest, isolation, sticky dues, review loop."""

from __future__ import annotations

import pytest

from app.services import store
from app.runtime.agent.learning import (
    build_review_digest,
    compact_tool_trail,
    observe_turn,
    reset_learning_state,
    run_learning_review,
    snapshot,
)
from app.runtime.agent.learning.state import begin_review, finish_review, in_review_scope
from app.runtime.agent.metrics import TurnMetrics
from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.llm.openai_compat import LLMRequestError
from app.runtime.tools.registry import reset_registry
from tests.fakes.llm import ScriptedLLM, text_reply


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch) -> None:
    original_path = getattr(store, "_path", None)
    reset_registry()
    reset_learning_state()
    store.rebind(tmp_path / "learn_h.db")
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "home")
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    store.update_settings(
        {
            "learning_enabled": True,
            "learning_memory_nudge_turns": 2,
            "learning_skill_nudge_iters": 4,
            "learning_cooldown_sec": 0,
        }
    )
    yield
    reset_learning_state()
    reset_registry()
    if original_path is not None:
        try:
            store.rebind(original_path)
        except Exception:
            pass


def test_digest_includes_goal_trail_skills() -> None:
    msgs = [
        {"role": "user", "content": "fix the flaky test"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": '{"cmd":"pytest -q"}',
                    },
                }
            ],
        },
        {"role": "tool", "name": "bash", "content": "1 failed"},
    ]
    d = build_review_digest(
        messages=msgs,
        user_message="fix the flaky test",
        final_content="Patched the race.",
        skills_touched=["python-unit-testing"],
        tool_calls=1,
        plan_reason="skill_touched",
        skill_catalog="- python-unit-testing: pytest patterns",
        user_snippet="Prefers concise answers",
    )
    assert "fix the flaky test" in d
    assert "bash" in d
    assert "python-unit-testing" in d
    assert "skill_touched" in d
    assert "Patched the race" in d
    assert "Existing skill catalog" in d
    assert "USER profile" in d
    assert "Refine-first" in d
    assert "[execution]" in d
    assert "[conversation" in d  # conversation and/or diary context labels
    assert "record_episode" in d or "Memory capacity" in d or "USER profile" in d


def test_cooldown_does_not_burn_nudge() -> None:
    store.update_settings({"learning_cooldown_sec": 9999})
    # Arm memory due
    p1 = observe_turn(agent_id="cd", tool_calls=0, ended_kind="final")
    assert p1 is None  # turn 1 of 2
    p2 = observe_turn(agent_id="cd", tool_calls=0, ended_kind="final")
    assert p2 is not None and p2.review_memory
    # Do not begin_review — cooldown after a finished review is the case we care about
    assert begin_review(p2) is True
    finish_review("cd", saved=False)

    # Re-arm memory (2 more turns)
    observe_turn(agent_id="cd", tool_calls=0, ended_kind="final")
    p = observe_turn(agent_id="cd", tool_calls=0, ended_kind="final")
    # Cooldown blocks returning a plan, but dues stick
    assert p is None
    snap = snapshot("cd")
    assert snap["memory_due"] is True

    # Clear cooldown clock
    store.update_settings({"learning_cooldown_sec": 0})
    from app.runtime.agent.learning.state import get_state

    get_state("cd").last_review_at = 0.0
    p3 = observe_turn(agent_id="cd", tool_calls=0, ended_kind="final")
    assert p3 is not None and p3.review_memory


async def test_review_saves_via_memory_tool() -> None:
    client = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="m1",
                        name="memory",
                        arguments={
                            "target": "user",
                            "action": "add",
                            "content": "Prefers concise answers",
                        },
                    )
                ],
            ),
            text_reply("Saved."),
        ]
    )
    _ = observe_turn(agent_id="main", tool_calls=0, ended_kind="final")
    plan = observe_turn(agent_id="main", tool_calls=0, ended_kind="final")
    assert plan and plan.review_memory

    metrics = TurnMetrics(
        agent_id="main", session_id="s1", ended_kind="final", tool_calls=0
    )
    result = await run_learning_review(
        client=client,
        messages=[{"role": "user", "content": "be shorter please"}],
        metrics=metrics,
        user_message="be shorter please",
        final_content="ok",
        plan=plan,
    )
    assert result is not None
    assert result["saved"] is True
    assert any("memory" in a for a in result["actions"])
    assert result.get("diary")
    events = store.list_learning_events(limit=5)
    assert any(e.get("saved") for e in events)


async def test_review_idle_still_records_event() -> None:
    client = ScriptedLLM([text_reply("Nothing to save.")])
    _ = observe_turn(agent_id="idle-a", tool_calls=0, ended_kind="final")
    plan = observe_turn(agent_id="idle-a", tool_calls=0, ended_kind="final")
    assert plan and plan.review_memory
    metrics = TurnMetrics(
        agent_id="idle-a", session_id="s-idle", ended_kind="final", tool_calls=0
    )
    result = await run_learning_review(
        client=client,
        messages=[{"role": "user", "content": "hi"}],
        metrics=metrics,
        user_message="hi",
        final_content="hello",
        plan=plan,
    )
    assert result is not None
    assert result["saved"] is False
    events = store.list_learning_events(limit=10, agent_id="idle-a")
    assert events
    assert events[0]["saved"] is False


def test_review_scope_blocks_nested_observe() -> None:
    from app.runtime.agent.learning.state import enter_review_scope, exit_review_scope

    token = enter_review_scope()
    assert in_review_scope()
    assert observe_turn(agent_id="x", tool_calls=99, ended_kind="final") is None
    exit_review_scope(token)
    assert not in_review_scope()


def test_trail_marks_errors() -> None:
    trail = compact_tool_trail(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "name": "bash", "content": "Error: boom"},
        ]
    )
    assert "✗" in trail
    assert "errors=1" in trail


async def test_review_llm_empty_choices_is_skipped_not_logged_as_error(
    monkeypatch,
) -> None:
    """A provider that returns empty choices should not spam the growth log."""
    import app.runtime.agent.retry as retry_mod

    real = retry_mod.with_llm_retry

    async def fast_retry(op, *, attempts=2, base_delay_s=0.75, label="llm"):
        return await real(op, attempts=attempts, base_delay_s=0.01, label=label)

    monkeypatch.setattr(retry_mod, "with_llm_retry", fast_retry)

    class EmptyChoicesLLM:
        async def complete(self, messages, tools=None):
            raise LLMRequestError(
                "LLM request failed: empty choices[] — provider returned no completion"
            )

    _ = observe_turn(agent_id="empty", tool_calls=0, ended_kind="final")
    plan = observe_turn(agent_id="empty", tool_calls=0, ended_kind="final")
    assert plan and plan.review_memory
    metrics = TurnMetrics(
        agent_id="empty", session_id="s-empty", ended_kind="final", tool_calls=0
    )
    result = await run_learning_review(
        client=EmptyChoicesLLM(),
        messages=[{"role": "user", "content": "hi"}],
        metrics=metrics,
        user_message="hi",
        final_content="hello",
        plan=plan,
    )
    assert result is None
    events = store.list_learning_events(limit=10, agent_id="empty")
    assert not events


async def test_review_llm_prefers_stream_complete(monkeypatch) -> None:
    """Learning review must use stream_complete (same path as chat), with tools."""
    import app.runtime.agent.retry as retry_mod

    real = retry_mod.with_llm_retry

    async def fast_retry(op, *, attempts=2, base_delay_s=0.75, label="llm"):
        return await real(op, attempts=attempts, base_delay_s=0.01, label=label)

    monkeypatch.setattr(retry_mod, "with_llm_retry", fast_retry)

    class StreamPreferLLM:
        def __init__(self) -> None:
            self.complete_calls = 0
            self.stream_calls = 0
            self.stream_had_tools = False

        async def complete(self, messages, tools=None):
            self.complete_calls += 1
            raise LLMRequestError(
                "LLM request failed: empty choices[] — provider returned no completion"
            )

        async def stream_complete(self, messages, tools=None):
            self.stream_calls += 1
            self.stream_had_tools = bool(tools)
            yield {"type": "done", "response": LLMResponse(content="Nothing to save.")}

    llm = StreamPreferLLM()
    _ = observe_turn(agent_id="stream-pref", tool_calls=0, ended_kind="final")
    plan = observe_turn(agent_id="stream-pref", tool_calls=0, ended_kind="final")
    assert plan and plan.review_memory
    metrics = TurnMetrics(
        agent_id="stream-pref",
        session_id="s-sp",
        ended_kind="final",
        tool_calls=0,
    )
    result = await run_learning_review(
        client=llm,
        messages=[{"role": "user", "content": "hi"}],
        metrics=metrics,
        user_message="hi",
        final_content="hello",
        plan=plan,
    )
    assert result is not None
    assert result.get("saved") is False
    assert "Nothing to save" in (result.get("note") or "")
    assert llm.stream_calls >= 1
    assert llm.stream_had_tools is True
    assert llm.complete_calls == 0
