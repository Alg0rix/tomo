"""Provider usage stays accurate across mixed providers and history replay."""

from types import SimpleNamespace

import pytest

from app.channels.sse_map import map_loop_event
from app.runtime.agent.loop import _record_response_usage
from app.runtime.agent.metrics import TurnMetrics, session_usage
from app.runtime.llm.base import LLMResponse
from app.runtime.llm.openai_compat import parse_usage_details
from app.services import store


@pytest.mark.parametrize("details, expected", [
    (None, None), ({}, None), ({"cached_tokens": 0}, 0),
    ({"cached_tokens": "12"}, 12), ({"cached_tokens": "bad"}, None),
    ({"cached_tokens": -5}, 0),
])
def test_optional_cache_details(details, expected):
    assert parse_usage_details({"prompt_tokens_details": details})["cached_tokens"] == expected
    sdk = SimpleNamespace(input_tokens_details=SimpleNamespace(**(details or {})))
    assert parse_usage_details(sdk)["cached_tokens"] == expected


def test_cache_rate_excludes_unknown_and_estimated_input():
    metrics = TurnMetrics(llm_rounds=4)
    metrics.add_usage(100, 10, cached_tokens=80, reasoning_tokens=3)
    metrics.add_usage(300, 20, cached_tokens=0)
    metrics.add_usage(500, 30)  # provider omitted cache details
    metrics.add_usage(1000, 40, estimated=True)
    result = session_usage([{"type": "final", "metrics": metrics.as_dict()}])
    assert result["cache_hit_rate"] == 20.0
    assert result["cache_prompt_tokens"] == 400
    assert result["prompt_tokens"] == 1900
    assert result["completion_tokens"] == 100
    assert result["reasoning_tokens"] == 3
    assert result["cache_reported_rounds"] == 2
    assert result["estimated_rounds"] == 1


def test_missing_usage_uses_estimate_without_inventing_cache():
    metrics = TurnMetrics()
    _record_response_usage(metrics, [{"role": "user", "content": "hello"}], LLMResponse(content="hi"))
    assert metrics.prompt_tokens > 0
    assert metrics.estimated_rounds == 1
    result = session_usage([{"type": "final", "metrics": metrics.as_dict()}])
    assert result["cache_hit_rate"] is None
    assert session_usage([{"type": "final", "content": "old history"}])["recorded_turns"] == 0


def test_usage_survives_event_mapping_and_database_refresh(tmp_path):
    store.rebind(tmp_path / "session-usage.db")
    sid = store.create_swarm_session(["main"])
    for kind, agent, prompt, cached in [("subagent_final", "ops", 300, 200), ("final", "main", 100, 80)]:
        metrics = TurnMetrics(agent_id=agent, llm_rounds=1, tool_calls=2)
        metrics.add_usage(prompt, 10, cached_tokens=cached)
        _, entries, _ = map_loop_event(
            {"kind": kind, "content": "done", "metrics": metrics.as_dict()}, agent, agent, 0, "turn1"
        )
        for entry in entries:
            store.append_session_history(sid, entry)
    history = store.get_session_history(sid)
    assert history[0]["metrics"]["cached_tokens"] == 200
    assert history[1]["params"] is None
    result = session_usage(history)
    assert result["cache_hit_rate"] == 70.0
    assert result["llm_rounds"] == 2
    assert result["tool_calls"] == 4
    assert result["completion_tokens"] == 20
    other = store.create_swarm_session(["main"])
    assert session_usage(store.get_session_history(other))["recorded_turns"] == 0
    store.clear_session_by_id(sid)
    assert session_usage(store.get_session_history(sid))["cache_hit_rate"] is None


async def test_context_endpoint_exposes_recorded_usage(tmp_path, monkeypatch):
    from app.api import rest
    from app.runtime.llm import context_window

    store.rebind(tmp_path / "context-usage.db")
    sid = store.create_swarm_session(["main"])
    metrics = TurnMetrics(llm_rounds=1)
    metrics.add_usage(100, 10, cached_tokens=75)
    store.append_session_history(sid, {"type": "final", "content": "done", "metrics": metrics.as_dict()})
    monkeypatch.setattr(rest, "require_owned_session", lambda request, session_id: store.get_session(session_id))

    async def resolve(agent_id):
        return 128_000

    monkeypatch.setattr(context_window, "resolve_context_window", resolve)
    result = await rest.session_context_usage(sid, None, None)
    assert result["limit"] == 128_000
    assert result["usage"]["cache_hit_rate"] == 75.0
    assert result["usage"]["prompt_tokens"] == 100


def test_coordinator_reviews_count_toward_tokens_and_cache_without_final_answer(tmp_path):
    from app.channels.web import _accumulate_turn_tokens
    store.rebind(tmp_path / "coordination-usage.db")
    sid = store.create_swarm_session(["main"])
    metrics = TurnMetrics(llm_rounds=1)
    metrics.add_usage(100, 10, cached_tokens=80)
    event = {"kind": "swarm_event", "event": "coordinator_note", "run_id": "r1",
             "event_id": 1, "content": "Sent guidance", "metrics": metrics.as_dict()}
    _, entries, _ = map_loop_event(event, "main", "Main", 0, "turn1")
    assert [e["type"] for e in entries] == ["coordination_metrics"]
    for entry in entries:
        store.append_session_history(sid, entry)
    usage = session_usage(store.get_session_history(sid))
    assert usage["cache_hit_rate"] == 80.0
    assert usage["prompt_tokens"] == 100
    tokens = {}
    _accumulate_turn_tokens(tokens, event)
    assert tokens == {"prompt": 100, "completion": 10}
