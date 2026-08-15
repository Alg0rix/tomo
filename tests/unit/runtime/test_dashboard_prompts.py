"""Unit tests for dynamic Home 'Try asking' prompt generation."""

from __future__ import annotations

import asyncio

import pytest

from app.runtime.dashboard_prompts import (
    _FALLBACK_POOL,
    build_user_context,
    clear_dashboard_prompts_cache,
    get_dashboard_prompts,
    parse_prompts_json,
)
from app.runtime.llm.base import LLMResponse
from app.runtime.llm.openai_compat import LLMConfigError
from app.services import store


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_dashboard_prompts_cache()
    yield
    clear_dashboard_prompts_cache()


_VALID_RAW = (
    '[{"key": "sprint-plan", "label": "Plan the sprint", "prompt": "Plan the next sprint"},'
    '{"key": "dep-audit", "label": "Audit dependencies", "prompt": "Audit outdated dependencies"},'
    '{"key": "market-scan", "label": "Scan the market", "prompt": "Scan the market for competitors"}]'
)


# ── parse_prompts_json ──────────────────────────────────────────────────


def test_parse_prompts_json_valid():
    parsed = parse_prompts_json(_VALID_RAW)
    assert parsed is not None
    assert len(parsed) == 3
    assert parsed[0] == {
        "key": "sprint-plan",
        "label": "Plan the sprint",
        "prompt": "Plan the next sprint",
    }


def test_parse_prompts_json_strips_markdown_fences():
    raw = "```json\n" + _VALID_RAW + "\n```"
    parsed = parse_prompts_json(raw)
    assert parsed is not None
    assert len(parsed) == 3


def test_parse_prompts_json_rejects_bad_json():
    assert parse_prompts_json("not json at all") is None


def test_parse_prompts_json_rejects_empty():
    assert parse_prompts_json("") is None
    assert parse_prompts_json(None) is None


def test_parse_prompts_json_rejects_wrong_count():
    two = '[{"key": "a", "label": "A", "prompt": "do a"}, {"key": "b", "label": "B", "prompt": "do b"}]'
    assert parse_prompts_json(two) is None
    four = (
        '[{"key": "a", "label": "A", "prompt": "do a"},'
        '{"key": "b", "label": "B", "prompt": "do b"},'
        '{"key": "c", "label": "C", "prompt": "do c"},'
        '{"key": "d", "label": "D", "prompt": "do d"}]'
    )
    assert parse_prompts_json(four) is None


def test_parse_prompts_json_rejects_wrapped_object():
    # Spec asks for a bare array; an unexpected wrapper structure is rejected.
    wrapped = '{"prompts": ' + _VALID_RAW + "}"
    assert parse_prompts_json(wrapped) is None


def test_parse_prompts_json_rejects_missing_field():
    raw = (
        '[{"key": "a", "prompt": "do a"},'
        '{"key": "b", "label": "B", "prompt": "do b"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )
    assert parse_prompts_json(raw) is None


def test_parse_prompts_json_rejects_empty_field():
    raw = (
        '[{"key": "", "label": "A", "prompt": "do a"},'
        '{"key": "b", "label": "B", "prompt": "do b"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )
    assert parse_prompts_json(raw) is None


def test_parse_prompts_json_rejects_duplicate_key():
    raw = (
        '[{"key": "a", "label": "A", "prompt": "do a"},'
        '{"key": "a", "label": "B", "prompt": "do b"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )
    assert parse_prompts_json(raw) is None


def test_parse_prompts_json_rejects_duplicate_key_case_insensitive():
    raw = (
        '[{"key": "Plan", "label": "A", "prompt": "do a"},'
        '{"key": "plan", "label": "B", "prompt": "do b"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )
    assert parse_prompts_json(raw) is None


def test_parse_prompts_json_rejects_duplicate_prompt():
    raw = (
        '[{"key": "a", "label": "A", "prompt": "same text"},'
        '{"key": "b", "label": "B", "prompt": "same text"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )
    assert parse_prompts_json(raw) is None


def test_parse_prompts_json_rejects_non_dict_item():
    raw = '["a", {"key": "b", "label": "B", "prompt": "do b"}, {"key": "c", "label": "C", "prompt": "do c"}]'
    assert parse_prompts_json(raw) is None


def test_parse_prompts_json_truncates_oversized_fields():
    long_prompt = "x " * 400  # far over the 300-char cap once collapsed
    raw = (
        '[{"key": "a", "label": "A", "prompt": "' + long_prompt + '"},'
        '{"key": "b", "label": "B", "prompt": "do b"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )
    parsed = parse_prompts_json(raw)
    assert parsed is not None
    assert len(parsed[0]["prompt"]) <= 300


# ── fallback pool ────────────────────────────────────────────────────────


def test_fallback_pool_has_enough_entries():
    assert len(_FALLBACK_POOL) >= 3
    for entry in _FALLBACK_POOL:
        assert entry["key"] and entry["label"] and entry["prompt"]


@pytest.mark.asyncio
async def test_get_dashboard_prompts_fallback_returns_three_distinct():
    class _Unconfigured:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model")

    for _ in range(10):
        clear_dashboard_prompts_cache()
        result = await get_dashboard_prompts("web", llm=_Unconfigured())
        assert result["source"] == "fallback"
        prompts = result["prompts"]
        assert len(prompts) == 3
        keys = {p["key"] for p in prompts}
        assert len(keys) == 3
        for p in prompts:
            assert p in _FALLBACK_POOL


# ── build_user_context (user isolation) ─────────────────────────────────


def test_build_user_context_empty_when_no_data(tmp_path):
    store.rebind(tmp_path / "ctx_empty.db")
    assert build_user_context("nobody") == ""


def test_build_user_context_scoped_to_user(tmp_path):
    store.rebind(tmp_path / "ctx_scope.db")
    store.create_knowledge_entry(
        {"title": "Alpha secret", "body": "only alice's note", "user_id": "alice"}
    )
    store.create_knowledge_entry(
        {"title": "Bob secret", "body": "only bob's note", "user_id": "bob"}
    )
    store.insert_episode(
        {
            "user_id": "alice",
            "objective": "alice objective",
            "outcome_summary": "alice outcome",
            "state": "active",
            "force": True,
        }
    )
    store.insert_episode(
        {
            "user_id": "bob",
            "objective": "bob objective",
            "outcome_summary": "bob outcome",
            "state": "active",
            "force": True,
        }
    )

    alice_ctx = build_user_context("alice")
    assert "Alpha secret" in alice_ctx
    assert "alice objective" in alice_ctx
    assert "Bob secret" not in alice_ctx
    assert "bob objective" not in alice_ctx

    bob_ctx = build_user_context("bob")
    assert "Bob secret" in bob_ctx
    assert "Alpha secret" not in bob_ctx


def test_build_user_context_truncates_long_body(tmp_path):
    store.rebind(tmp_path / "ctx_trunc.db")
    store.create_knowledge_entry(
        {"title": "Long", "body": "z" * 500, "user_id": "alice"}
    )
    ctx = build_user_context("alice")
    assert "z" * 500 not in ctx
    assert "z" * 160 in ctx


# ── get_dashboard_prompts orchestration ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_dashboard_prompts_uses_llm_on_valid_response():
    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content=_VALID_RAW, tool_calls=[])

    result = await get_dashboard_prompts("web", llm=_L())
    assert result["source"] == "llm"
    assert result["prompts"][0]["key"] == "sprint-plan"


@pytest.mark.asyncio
async def test_get_dashboard_prompts_caches_per_user():
    calls = {"n": 0}

    class _L:
        async def complete(self, messages, tools=None):
            calls["n"] += 1
            return LLMResponse(content=_VALID_RAW, tool_calls=[])

    first = await get_dashboard_prompts("alice", llm=_L())
    assert first["source"] == "llm"
    assert calls["n"] == 1

    # Second call for same user hits cache — llm not invoked even if provided.
    second = await get_dashboard_prompts("alice", llm=_L())
    assert second["source"] == "llm"
    assert second["prompts"] == first["prompts"]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_get_dashboard_prompts_cache_isolated_per_user():
    class _LA:
        async def complete(self, messages, tools=None):
            return LLMResponse(content=_VALID_RAW, tool_calls=[])

    other_raw = (
        '[{"key": "x", "label": "X", "prompt": "do x"},'
        '{"key": "y", "label": "Y", "prompt": "do y"},'
        '{"key": "z", "label": "Z", "prompt": "do z"}]'
    )

    class _LB:
        async def complete(self, messages, tools=None):
            return LLMResponse(content=other_raw, tool_calls=[])

    a = await get_dashboard_prompts("alice", llm=_LA())
    b = await get_dashboard_prompts("bob", llm=_LB())
    assert a["prompts"][0]["key"] == "sprint-plan"
    assert b["prompts"][0]["key"] == "x"


@pytest.mark.asyncio
async def test_get_dashboard_prompts_cache_expires(monkeypatch):
    import app.runtime.dashboard_prompts as dp

    calls = {"n": 0}

    class _L:
        async def complete(self, messages, tools=None):
            calls["n"] += 1
            return LLMResponse(content=_VALID_RAW, tool_calls=[])

    fake_time = {"t": 1000.0}
    monkeypatch.setattr(dp.time, "monotonic", lambda: fake_time["t"])

    await get_dashboard_prompts("alice", llm=_L())
    assert calls["n"] == 1

    # Still within TTL.
    fake_time["t"] += 60 * 60
    await get_dashboard_prompts("alice", llm=_L())
    assert calls["n"] == 1

    # Past the 90-minute TTL.
    fake_time["t"] += 40 * 60
    await get_dashboard_prompts("alice", llm=_L())
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_get_dashboard_prompts_llm_config_error_falls_back():
    class _L:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model configured")

    result = await get_dashboard_prompts("web", llm=_L())
    assert result["source"] == "fallback"
    assert len(result["prompts"]) == 3


@pytest.mark.asyncio
async def test_get_dashboard_prompts_llm_raises_falls_back():
    class _L:
        async def complete(self, messages, tools=None):
            raise RuntimeError("provider blew up")

    result = await get_dashboard_prompts("web", llm=_L())
    assert result["source"] == "fallback"


@pytest.mark.asyncio
async def test_get_dashboard_prompts_malformed_output_falls_back():
    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content="not json", tool_calls=[])

    result = await get_dashboard_prompts("web", llm=_L())
    assert result["source"] == "fallback"


@pytest.mark.asyncio
async def test_get_dashboard_prompts_duplicate_output_falls_back():
    dup_raw = (
        '[{"key": "a", "label": "A", "prompt": "same"},'
        '{"key": "a", "label": "B", "prompt": "same"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )

    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content=dup_raw, tool_calls=[])

    result = await get_dashboard_prompts("web", llm=_L())
    assert result["source"] == "fallback"


@pytest.mark.asyncio
async def test_get_dashboard_prompts_timeout_falls_back(monkeypatch, caplog):
    import logging

    import app.runtime.dashboard_prompts as dp

    monkeypatch.setattr(dp, "_LLM_TIMEOUT_S", 0.01)

    class _Slow:
        async def complete(self, messages, tools=None):
            await asyncio.sleep(0.2)
            return LLMResponse(content=_VALID_RAW, tool_calls=[])

    with caplog.at_level(logging.WARNING, logger="app.runtime.dashboard_prompts"):
        result = await get_dashboard_prompts("web", llm=_Slow())
    assert result["source"] == "fallback"
    assert any("timed out" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)
