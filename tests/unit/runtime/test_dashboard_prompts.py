"""Unit tests for dynamic Home 'Try asking' prompt generation."""

from __future__ import annotations

from app.runtime.memory.vault import write
from app.runtime.memory.vault.notes import scoped_key

import asyncio

import pytest

from app.runtime.dashboard_prompts import (
    _FALLBACK_POOL,
    _memory_fallback,
    _memory_signals,
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




def test_parse_prompts_json_strips_markdown_fences():
    raw = "```json\n" + _VALID_RAW + "\n```"
    parsed = parse_prompts_json(raw)
    assert parsed is not None
    assert len(parsed) == 3




def test_parse_prompts_json_rejects_empty():
    assert parse_prompts_json("") is None
    assert parse_prompts_json(None) is None




def test_parse_prompts_json_rejects_wrapped_object():
    # Spec asks for a bare array; an unexpected wrapper structure is rejected.
    wrapped = '{"prompts": ' + _VALID_RAW + "}"
    assert parse_prompts_json(wrapped) is None




def test_parse_prompts_json_rejects_empty_field():
    raw = (
        '[{"key": "", "label": "A", "prompt": "do a"},'
        '{"key": "b", "label": "B", "prompt": "do b"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )
    assert parse_prompts_json(raw) is None








def test_parse_prompts_json_rejects_non_dict_item():
    raw = '["a", {"key": "b", "label": "B", "prompt": "do b"}, {"key": "c", "label": "C", "prompt": "do c"}]'
    assert parse_prompts_json(raw) is None




# ── fallback pool ────────────────────────────────────────────────────────




@pytest.mark.asyncio
async def test_get_dashboard_prompts_fallback_returns_three_distinct(tmp_path):
    store.rebind(tmp_path / "fb_generic.db")

    class _Unconfigured:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model")

    for _ in range(10):
        clear_dashboard_prompts_cache()
        result = await get_dashboard_prompts("fb_nobody", llm=_Unconfigured())
        assert result["source"] == "fallback"
        prompts = result["prompts"]
        assert len(prompts) == 3
        keys = {p["key"] for p in prompts}
        assert len(keys) == 3
        for p in prompts:
            assert p in _FALLBACK_POOL


@pytest.mark.asyncio
async def test_get_dashboard_prompts_memory_fallback_from_sessions(tmp_path):
    store.rebind(tmp_path / "fb_mem_sess.db")
    _make_session("fb_alice", "Help me debug the CCTV lane dashboard relay")

    class _Unconfigured:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model")

    result = await get_dashboard_prompts("fb_alice", llm=_Unconfigured())
    assert result["source"] == "fallback-memory"
    prompts = result["prompts"]
    assert len(prompts) == 3
    blob = " ".join(p["prompt"] for p in prompts)
    assert "CCTV lane dashboard" in blob
    assert len({p["key"] for p in prompts}) == 3


@pytest.mark.asyncio
async def test_get_dashboard_prompts_memory_fallback_from_vault(tmp_path):
    store.rebind(tmp_path / "fb_mem_vault.db")
    write.add_entity(
        'fb_vault_user',
        scoped_key('topic', 'Sourdough'),
        'Sourdough: bakes sourdough every weekend',
    )

    class _Unconfigured:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model")

    result = await get_dashboard_prompts("fb_vault_user", llm=_Unconfigured())
    assert result["source"] == "fallback-memory"
    blob = " ".join(p["prompt"] for p in result["prompts"])
    assert "sourdough" in blob.lower()


@pytest.mark.asyncio
async def test_get_dashboard_prompts_memory_fallback_isolated(tmp_path):
    store.rebind(tmp_path / "fb_mem_iso.db")
    _make_session("fb_iso_alice", "alice cctv project follow-up")
    _make_session("fb_iso_bob", "bob cooking recipe ideas")

    class _Unconfigured:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model")

    alice = await get_dashboard_prompts("fb_iso_alice", llm=_Unconfigured())
    assert alice["source"] == "fallback-memory"
    assert "cctv" in " ".join(p["prompt"] for p in alice["prompts"]).lower()
    assert "cooking" not in " ".join(p["prompt"] for p in alice["prompts"]).lower()

    clear_dashboard_prompts_cache()
    bob = await get_dashboard_prompts("fb_iso_bob", llm=_Unconfigured())
    assert bob["source"] == "fallback-memory"
    assert "cooking" in " ".join(p["prompt"] for p in bob["prompts"]).lower()
    assert "cctv" not in " ".join(p["prompt"] for p in bob["prompts"]).lower()


@pytest.mark.asyncio
async def test_get_dashboard_prompts_memory_fallback_survives_store_error(
    tmp_path, monkeypatch
):
    store.rebind(tmp_path / "fb_mem_fail.db")

    monkeypatch.setattr(
        store, "list_sessions",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    monkeypatch.setattr(
        store, "list_episodes",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    class _Unconfigured:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model")

    result = await get_dashboard_prompts("anyone", llm=_Unconfigured())
    assert result["source"] == "fallback"
    assert len(result["prompts"]) == 3


def test_memory_signals_prefers_sessions_over_episodes(tmp_path):
    store.rebind(tmp_path / "fb_sig_order.db")
    _make_session("fb_sig_user", "session question about relays")
    store.insert_episode(
        {
            "user_id": "fb_sig_user",
            "objective": "episode objective about ovens",
            "state": "active",
            "force": True,
        }
    )
    signals = _memory_signals("fb_sig_user")
    assert signals and signals[0][0] == "query"
    assert any(s[0] == "episode" for s in signals) or len(signals) >= 1


def test_memory_fallback_returns_none_without_signals(tmp_path):
    store.rebind(tmp_path / "fb_sig_none.db")
    assert _memory_fallback("fb_sig_nobody") is None


# ── build_user_context (user isolation) ─────────────────────────────────


def test_build_user_context_empty_when_no_data(tmp_path):
    store.rebind(tmp_path / "ctx_empty.db")
    assert build_user_context("nobody") == ""


def test_build_user_context_scoped_to_user(tmp_path):
    store.rebind(tmp_path / "ctx_scope.db")
    write.add_entity('alice', scoped_key('topic', 'Alpha secret'), 'Alpha secret' + ': ' + "only alice's note")
    write.add_entity('bob', scoped_key('topic', 'Bob secret'), 'Bob secret' + ': ' + "only bob's note")
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
    write.add_entity('alice', scoped_key('topic', 'Long'), 'Long' + ': ' + 'z' * 500)
    ctx = build_user_context("alice")
    assert "z" * 500 not in ctx
    assert "z" * 160 in ctx


# ── build_user_context (recent sessions) ──────────────────────────────────


def _make_session(user_id: str, *queries: str) -> str:
    sid = store.create_swarm_session(["main"], user_id=user_id)
    for q in queries:
        store.append_session_history(sid, {"type": "user", "content": q})
    return sid


def test_build_user_context_includes_recent_sessions(tmp_path):
    store.rebind(tmp_path / "ctx_sess.db")
    _make_session("sess_alice", "Help me debug the CCTV lane dashboard relay")
    ctx = build_user_context("sess_alice")
    assert "Recent sessions:" in ctx
    assert "CCTV lane dashboard" in ctx


def test_build_user_context_sessions_scoped_to_user(tmp_path):
    store.rebind(tmp_path / "ctx_sess_scope.db")
    _make_session("sess_scope_alice", "alice cctv project follow-up")
    _make_session("sess_scope_bob", "bob cooking recipe ideas")
    alice_ctx = build_user_context("sess_scope_alice")
    assert "cctv" in alice_ctx.lower()
    assert "cooking" not in alice_ctx.lower()
    bob_ctx = build_user_context("sess_scope_bob")
    assert "cooking" in bob_ctx.lower()
    assert "cctv" not in bob_ctx.lower()


def test_build_user_context_skips_empty_sessions(tmp_path):
    store.rebind(tmp_path / "ctx_sess_empty.db")
    # No messages yet — title stays "New conversation", no signal.
    store.create_swarm_session(["main"], user_id="sess_empty_user")
    assert build_user_context("sess_empty_user") == ""


def test_build_user_context_survives_session_store_failure(tmp_path, monkeypatch):
    store.rebind(tmp_path / "ctx_sess_fail.db")
    write.add_entity('sess_fail_user', scoped_key('topic', 'Fail secret'), 'Fail secret' + ': ' + "only this user's note")
    monkeypatch.setattr(
        store, "list_sessions",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    ctx = build_user_context("sess_fail_user")
    assert "Fail secret" in ctx
    assert "Recent sessions:" not in ctx


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
async def test_get_dashboard_prompts_llm_config_error_falls_back(tmp_path):
    store.rebind(tmp_path / "fb_llm_err.db")

    class _L:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model configured")

    result = await get_dashboard_prompts("fb_llm_err_user", llm=_L())
    assert result["source"] == "fallback"
    assert len(result["prompts"]) == 3


@pytest.mark.asyncio
async def test_get_dashboard_prompts_llm_raises_falls_back(tmp_path):
    store.rebind(tmp_path / "fb_llm_raise.db")

    class _L:
        async def complete(self, messages, tools=None):
            raise RuntimeError("provider blew up")

    result = await get_dashboard_prompts("fb_llm_raise_user", llm=_L())
    assert result["source"] == "fallback"


@pytest.mark.asyncio
async def test_get_dashboard_prompts_malformed_output_falls_back(tmp_path):
    store.rebind(tmp_path / "fb_llm_mal.db")

    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content="not json", tool_calls=[])

    result = await get_dashboard_prompts("fb_llm_mal_user", llm=_L())
    assert result["source"] == "fallback"


@pytest.mark.asyncio
async def test_get_dashboard_prompts_duplicate_output_falls_back(tmp_path):
    store.rebind(tmp_path / "fb_llm_dup.db")
    dup_raw = (
        '[{"key": "a", "label": "A", "prompt": "same"},'
        '{"key": "a", "label": "B", "prompt": "same"},'
        '{"key": "c", "label": "C", "prompt": "do c"}]'
    )

    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content=dup_raw, tool_calls=[])

    result = await get_dashboard_prompts("fb_llm_dup_user", llm=_L())
    assert result["source"] == "fallback"


@pytest.mark.asyncio
async def test_get_dashboard_prompts_timeout_falls_back(monkeypatch, caplog, tmp_path):
    import logging

    import app.runtime.dashboard_prompts as dp

    store.rebind(tmp_path / "fb_llm_timeout.db")
    monkeypatch.setattr(dp, "_LLM_TIMEOUT_S", 0.01)

    class _Slow:
        async def complete(self, messages, tools=None):
            await asyncio.sleep(0.2)
            return LLMResponse(content=_VALID_RAW, tool_calls=[])

    with caplog.at_level(logging.WARNING, logger="app.runtime.dashboard_prompts"):
        result = await get_dashboard_prompts("fb_llm_timeout_user", llm=_Slow())
    assert result["source"] == "fallback"
    assert any("timed out" in r.message for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records)
