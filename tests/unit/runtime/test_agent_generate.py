"""Unit tests for LLM agent draft generation."""

from __future__ import annotations

import pytest

from app.runtime.agent_generate import generate_agent_draft, parse_agent_draft_json
from app.runtime.llm.base import LLMResponse
from app.runtime.llm.openai_compat import LLMConfigError


def test_parse_agent_draft_json_plain() -> None:
    raw = '{"name": "NetOps", "role": "ops", "description": "Monitors infra."}'
    assert parse_agent_draft_json(raw) == {
        "name": "NetOps",
        "role": "ops",
        "description": "Monitors infra.",
    }


def test_parse_agent_draft_json_fenced() -> None:
    raw = '```json\n{"name": "Coder", "role": "coding", "description": "Writes code."}\n```'
    assert parse_agent_draft_json(raw)["name"] == "Coder"


def test_parse_agent_draft_json_rejects_empty_name() -> None:
    assert parse_agent_draft_json('{"name": "", "role": "x"}') is None


@pytest.mark.asyncio
async def test_generate_agent_draft_uses_llm() -> None:
    class _L:
        async def complete(self, messages, tools=None):
            return LLMResponse(
                content='{"name": "NetOps", "role": "ops", "description": "Network specialist."}',
                tool_calls=[],
            )

    draft = await generate_agent_draft("network ops agent", llm=_L())
    assert draft is not None
    assert draft["name"] == "NetOps"
    assert draft["role"] == "ops"
    assert draft["suggested_id"] == "netops"


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
