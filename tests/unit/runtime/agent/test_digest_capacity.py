"""Learning digest memory-capacity guidance."""

from __future__ import annotations

from app.runtime.agent.learning.digest import (
    build_review_digest,
    format_memory_capacity,
)


def test_format_memory_capacity_flags_full() -> None:
    text = format_memory_capacity(
        user_chars=1900,
        user_limit=2000,
        user_entries=12,
        agent_chars=500,
        agent_limit=4000,
        agent_entries=3,
    )
    assert "FULL" in text or "tight" in text
    assert "remember" in text.lower()
    assert "NEVER" in text or "never" in text.lower()


def test_digest_includes_capacity_and_overflow_rule() -> None:
    cap = format_memory_capacity(
        user_chars=100,
        user_limit=2000,
        user_entries=1,
        agent_chars=3500,
        agent_limit=4000,
        agent_entries=20,
    )
    digest = build_review_digest(
        messages=[{"role": "user", "content": "hi"}],
        user_message="hi",
        final_content="ok",
        skills_touched=[],
        memory_capacity=cap,
    )
    assert "## Memory capacity" in digest
    assert "do not create a skill as overflow" in digest.lower()
