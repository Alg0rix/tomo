"""Unit tests for LLM session auto-title helpers."""

from __future__ import annotations

import pytest

from app.runtime.llm.base import LLMResponse
from app.runtime.session_title import (
    first_user_and_final,
    generate_session_title,
    llm_title_skip_reason,
    sanitize_llm_title,
    should_llm_title,
)


def test_sanitize_strips_quotes_and_truncates() -> None:
    assert sanitize_llm_title('  "Q3 Launch Plan"  ') == "Q3 Launch Plan"
    assert sanitize_llm_title("") is None
    assert sanitize_llm_title("   ") is None
    long = sanitize_llm_title("x" * 80)
    assert long is not None
    assert long.endswith("…")
    assert len(long) <= 61


def test_first_user_and_final() -> None:
    assert first_user_and_final([]) is None
    assert first_user_and_final([{"type": "user", "content": "hi"}]) is None
    pair = first_user_and_final(
        [
            {"type": "user", "content": "hi"},
            {"type": "final", "content": "hello"},
        ]
    )
    assert pair == ("hi", "hello")


def test_should_llm_title_only_first_completed_turn() -> None:
    hist = [
        {"type": "user", "content": "Plan the Q3 launch carefully"},
        {"type": "final", "content": "Here is a plan..."},
    ]
    s = {"title": "Plan the Q3 launch carefully"}
    assert should_llm_title(s, hist) is True
    assert llm_title_skip_reason(s, hist) is None
    assert should_llm_title({"title": "Q3 Launch Plan"}, hist) is False
    assert "already set" in (llm_title_skip_reason({"title": "Q3 Launch Plan"}, hist) or "")
    assert should_llm_title(s, hist + [{"type": "user", "content": "more"}]) is False
    assert should_llm_title(None, hist) is False
    assert llm_title_skip_reason(None, hist) == "no session"


@pytest.mark.asyncio
async def test_generate_uses_llm_and_sanitizes() -> None:
    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content=' "Billing Follow-up" ', tool_calls=[])

    title = await generate_session_title("help with invoice", "sure", llm=_L())
    assert title == "Billing Follow-up"


@pytest.mark.asyncio
async def test_generate_returns_none_on_failure() -> None:
    class _Boom:
        async def complete(self, messages, tools=None):
            raise RuntimeError("nope")

    assert await generate_session_title("a", "b", llm=_Boom()) is None
