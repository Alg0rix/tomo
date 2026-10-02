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


async def test_stream_complete_yields_deltas_then_done() -> None:
    llm = ScriptedLLM([text_reply("hi there")])
    events = [ev async for ev in llm.stream_complete([])]
    assert events[-1]["type"] == "done"
    assert isinstance(events[-1]["response"], LLMResponse)
    assert events[-1]["response"].content == "hi there"
    deltas = [e["content"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "hi there"


async def test_bash_then_text_helper() -> None:
    queue = tool_then_text(bash_call("echo 1"), "done")
    llm = ScriptedLLM(queue)
    first = await llm.complete([], tools=[{"type": "function", "function": {"name": "bash"}}])
    assert first.has_tool_calls
    assert first.tool_calls[0].arguments == {"command": "echo 1"}
    second = await llm.complete([])
    assert second.content == "done"
