"""Context usage estimation tests — post-compression token counting.

Covers :func:`compute_context_usage`: empty history, long histories that
trigger :func:`maybe_compress_messages`, token accounting, percent clamping,
and ``_resolve_context_limit`` model matching.
"""
from __future__ import annotations

from typing import Any

from app.runtime.agent.context_usage import (
    _DEFAULT_CONTEXT,
    _resolve_context_limit,
    compute_context_usage,
    estimate_tokens,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _noop_store(monkeypatch) -> None:
    """Stub out every store dependency so tests run without a real DB."""
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "test-model"})
    monkeypatch.setattr(store, "list_models", lambda: [{"id": "test-model", "context": 128_000}])
    monkeypatch.setattr(store, "list_safety_rules", lambda: [])
    monkeypatch.setattr(store, "get_agent_skills", lambda aid: [])
    monkeypatch.setattr(store, "get_agent_openai_tools", lambda aid: [])


def _stub_prompt_builders(monkeypatch) -> None:
    """Return deterministic prompt text so results don't depend on filesystem."""
    import app.runtime.agent.context_usage as mod

    monkeypatch.setattr(mod, "build_system_prompt", lambda aid: "You are Tomo.")
    monkeypatch.setattr(mod, "_swarm_agents_prompt_section", lambda aid: "")
    monkeypatch.setattr(mod, "_workplace_prompt_section", lambda aid: "")


def _history(*entries: dict[str, Any]) -> list[dict[str, Any]]:
    """Raw store-format history entries (the shape ``history_to_messages`` expects)."""
    return list(entries)


# ── empty / minimal ─────────────────────────────────────────────────────


def test_empty_history(monkeypatch) -> None:
    """No history → conversation section is absent or zero, used ≈ system overhead."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    result = compute_context_usage("main", history=None)

    conv = next((s for s in result["sections"] if s["id"] == "conversation"), None)
    assert conv is None or conv["tokens"] == 0

    summarized = next((s for s in result["sections"] if s["id"] == "summarized_conversation"), None)
    assert summarized is None

    assert result["percent"] >= 0
    assert result["over_limit"] is False
    assert result["compressed"] is False


def test_single_user_message(monkeypatch) -> None:
    """One user message counts as conversation, not summarized."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    history = _history({"type": "user", "content": "hello"})
    result = compute_context_usage("main", history=history)

    conv = next(s for s in result["sections"] if s["id"] == "conversation")
    assert conv["tokens"] > 0

    summarized = next((s for s in result["sections"] if s["id"] == "summarized_conversation"), None)
    assert summarized is None
    assert result["compressed"] is False


# ── compression ─────────────────────────────────────────────────────────


def _big_history(n_exchanges: int = 60, msg_chars: int = 1000) -> list[dict[str, Any]]:
    """Build a long conversation history that exceeds the soft compress budget.

    Each exchange is a user message + assistant reply, so the resulting
    message list has ``2 * n_exchanges`` messages.  Default params produce
    ~120 messages × ~250 tokens ≈ 30K tokens, well over the 24K soft limit.
    """
    entries: list[dict[str, Any]] = []
    for i in range(n_exchanges):
        entries.append({"type": "user", "content": f"q{i}: " + "A" * msg_chars})
        entries.append({"type": "final", "content": f"a{i}: " + "B" * msg_chars})
    return entries


def test_long_history_triggers_compression(monkeypatch) -> None:
    """After compression, used << raw sum; summarized_conversation > 0."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    history = _big_history()
    result = compute_context_usage("main", history=history)

    assert result["compressed"] is True

    sum_sec = next((s for s in result["sections"] if s["id"] == "summarized_conversation"), None)
    assert sum_sec is not None
    assert sum_sec["tokens"] > 0

    conv_sec = next((s for s in result["sections"] if s["id"] == "conversation"), None)
    # Recent messages survive compression.
    assert conv_sec is not None and conv_sec["tokens"] > 0

    # The compressed total must be well below what the raw history alone would be.
    raw_conv_tokens = estimate_tokens("A" * 1000 + "B" * 1000) * 60
    assert result["used"] < raw_conv_tokens


def test_short_history_stays_uncompressed(monkeypatch) -> None:
    """A short conversation is not compressed."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    history = _history(
        {"type": "user", "content": "hi"},
        {"type": "final", "content": "hello"},
    )
    result = compute_context_usage("main", history=history)

    assert result["compressed"] is False
    sum_sec = next((s for s in result["sections"] if s["id"] == "summarized_conversation"), None)
    assert sum_sec is None


# ── accounting invariants ───────────────────────────────────────────────


def test_used_equals_sum_of_sections(monkeypatch) -> None:
    """``used`` must equal the sum of all section tokens."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    history = _big_history(30)
    result = compute_context_usage("main", history=history)

    section_sum = sum(s["tokens"] for s in result["sections"])
    assert result["used"] == section_sum


def test_percent_clamped_0_to_100(monkeypatch) -> None:
    """Percent is always 0–100 (CSS ring safety)."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    result = compute_context_usage("main", history=None)
    assert 0 <= result["percent"] <= 100


def test_over_limit_flag(monkeypatch) -> None:
    """over_limit is True when used exceeds limit."""
    from app.services import store

    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)
    # Tiny context window to force over-limit.
    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "tiny"})
    monkeypatch.setattr(store, "list_models", lambda: [{"id": "tiny", "context": 500}])

    history = _big_history(20)
    result = compute_context_usage("main", history=history)

    assert result["over_limit"] is True
    assert result["percent"] == 100  # clamped for ring
    assert result["used"] > result["limit"]


# ── _resolve_context_limit ──────────────────────────────────────────────


def test_resolve_exact_model_match(monkeypatch) -> None:
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "gpt-4o"})
    monkeypatch.setattr(store, "list_models", lambda: [
        {"id": "gpt-4o", "context": 128_000},
        {"id": "gpt-4o-mini", "context": 128_000},
    ])
    assert _resolve_context_limit("main") == 128_000


def test_resolve_prefix_match_longest_first(monkeypatch) -> None:
    """gpt-4o-2024-08-06 matches the specific id, not the shorter gpt-4o prefix."""
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "gpt-4o-2024-08-06"})
    monkeypatch.setattr(store, "list_models", lambda: [
        {"id": "gpt-4o", "context": 128_000},
        {"id": "gpt-4o-2024-08-06", "context": 131_072},
    ])
    assert _resolve_context_limit("main") == 131_072


def test_resolve_falls_back_to_default(monkeypatch) -> None:
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "unknown-model"})
    monkeypatch.setattr(store, "list_models", lambda: [
        {"id": "gpt-4o", "context": 128_000},
    ])
    assert _resolve_context_limit("main") == _DEFAULT_CONTEXT


def test_resolve_empty_model_returns_default(monkeypatch) -> None:
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": ""})
    monkeypatch.setattr(store, "list_models", lambda: [])
    assert _resolve_context_limit("main") == _DEFAULT_CONTEXT


# ── user_message parameter ──────────────────────────────────────────────


def test_user_message_included_in_conversation(monkeypatch) -> None:
    """The pending user message counts toward conversation tokens."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    result_no_msg = compute_context_usage("main", history=None)
    result_with_msg = compute_context_usage(
        "main", history=None, user_message="x" * 1000,
    )
    assert result_with_msg["used"] > result_no_msg["used"]


# ── limit parameter ───────────────────────────────────────────────


def test_explicit_limit_overrides_sync_resolve(monkeypatch) -> None:
    """Passing ``limit=500`` overrides the sync resolver."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    result = compute_context_usage("main", history=None, limit=500)
    assert result["limit"] == 500


def test_explicit_limit_over_limit_flag(monkeypatch) -> None:
    """over_limit is True when used exceeds an explicit small limit."""
    _noop_store(monkeypatch)
    _stub_prompt_builders(monkeypatch)

    history = _big_history(20)
    result = compute_context_usage("main", history=history, limit=500)

    assert result["over_limit"] is True
    assert result["limit"] == 500
    assert result["used"] > 500
