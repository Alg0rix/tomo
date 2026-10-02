"""ScriptedLLM fake — queue pops and streaming."""

from __future__ import annotations

import pytest

from app.runtime.llm.base import LLMClient, LLMResponse
from tests.fakes.llm import ScriptedLLM, bash_call, text_reply, tool_then_text


def test_scripted_satisfies_llm_client_protocol() -> None:
    assert isinstance(ScriptedLLM([text_reply("hi")]), LLMClient)


async def test_complete_pops_responses_in_order() -> None:
    llm = ScriptedLLM([text_reply("one"), text_reply("two")])
    assert (await llm.complete([])).content == "one"
    assert (await llm.complete([])).content == "two"
    assert llm.remaining == 0


async def test_complete_raises_when_queue_empty() -> None:
    llm = ScriptedLLM([text_reply("only")])
    await llm.complete([])
    with pytest.raises(AssertionError, match="no responses left"):
        await llm.complete([])




