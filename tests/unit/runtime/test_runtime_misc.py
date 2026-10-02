"""Consolidated tests (merged from: test_agent_generate.py, test_session_title.py).
- test_agent_generate.py: Unit tests for LLM agent draft generation.
- test_session_title.py: Unit tests for LLM session auto-title helpers.
"""

from __future__ import annotations

import pytest
from app.runtime.agent_generate import generate_agent_draft, parse_agent_draft_json
from app.runtime.llm.base import LLMResponse
from app.runtime.llm.openai_compat import LLMConfigError
from app.runtime.session_title import (
    first_user_and_final,
    generate_session_title,
    llm_title_skip_reason,
    sanitize_llm_title,
    should_llm_title,
)


# --- from test_agent_generate.py ---


def test_parse_agent_draft_json_fenced() -> None:
    raw = '```json\n{"name": "Coder", "role": "coding", "description": "Writes code."}\n```'
    assert parse_agent_draft_json(raw)["name"] == "Coder"


def test_parse_agent_draft_json_rejects_empty_name() -> None:
    assert parse_agent_draft_json('{"name": "", "role": "x"}') is None




@pytest.mark.asyncio
async def test_generate_agent_draft_fallback_system_prompt() -> None:
    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(
                content='{"name": "NetOps", "role": "ops", "description": "Network specialist."}',
                tool_calls=[],
            )

    draft = await generate_agent_draft("network ops agent", llm=_L())
    assert draft is not None
    assert "## Responsibilities" in draft["system_prompt"]


@pytest.mark.asyncio
async def test_generate_agent_draft_returns_none_on_bad_json() -> None:
    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content="not json", tool_calls=[])

    assert await generate_agent_draft("brief", llm=_L()) is None


@pytest.mark.asyncio
async def test_generate_agent_draft_propagates_config_error() -> None:
    class _L:
        async def complete(self, messages, tools=None):
            raise LLMConfigError("no model")

    with pytest.raises(LLMConfigError):
        await generate_agent_draft("brief", llm=_L())


# --- from test_session_title.py ---
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
async def test_generate_resolves_agent_model(monkeypatch) -> None:
    resolved: list[str | None] = []

    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(content="Service Config Check", tool_calls=[])

    def _get_llm(agent_id=None):
        resolved.append(agent_id)
        return _L()

    monkeypatch.setattr("app.runtime.llm.get_llm", _get_llm)
    assert await generate_session_title("check config", "done", agent_id="main") == "Service Config Check"
    assert resolved == ["main"]




