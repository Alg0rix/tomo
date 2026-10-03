import json
from types import SimpleNamespace

import pytest

from app.runtime import dashboard_prompts, plugin_ideas
from app.runtime.llm.base import LLMResponse


@pytest.fixture(autouse=True)
def ideas_context(monkeypatch):
    plugin_ideas._cache.clear()
    monkeypatch.setattr(
        dashboard_prompts, "build_user_context", lambda uid: "Activity for " + uid
    )
    monkeypatch.setattr(
        "app.plugins.manager.get_manager", lambda: SimpleNamespace(list=lambda: [])
    )
    yield
    plugin_ideas._cache.clear()


class Model:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, tools=None):
        self.calls.append(messages)
        return LLMResponse(
            content=json.dumps(
                [
                    {
                        "key": str(i),
                        "label": "Idea " + str(i),
                        "prompt": "Build a plugin " + str(i),
                    }
                    for i in range(3)
                ]
            ),
            tool_calls=[],
        )


@pytest.mark.asyncio
async def test_plugin_ideas_context_cache_and_refresh():
    model = Model()
    first = await plugin_ideas.get_plugin_ideas("alice", llm=model)
    assert first["source"] == "llm"
    assert "Tomo plugins" in model.calls[0][0]["content"]
    assert "Activity for alice" in model.calls[0][1]["content"]
    assert await plugin_ideas.get_plugin_ideas("alice", llm=model) == first
    assert len(model.calls) == 1
    await plugin_ideas.get_plugin_ideas("alice", refresh=True, llm=model)
    assert len(model.calls) == 2
    await plugin_ideas.get_plugin_ideas("bob", llm=model)
    assert len(model.calls) == 3
    assert "Activity for bob" in model.calls[-1][1]["content"]
    assert "alice" not in model.calls[-1][1]["content"]
    await dashboard_prompts.get_dashboard_prompts("alice", llm=model)
    assert len(model.calls) == 4, "Plugin suggestions cannot fill dashboard cache"


@pytest.mark.asyncio
async def test_plugin_ideas_invalidate_when_plugins_change(monkeypatch):
    plugins = []
    monkeypatch.setattr(
        "app.plugins.manager.get_manager", lambda: SimpleNamespace(list=lambda: plugins)
    )
    model = Model()
    await plugin_ideas.get_plugin_ideas("alice", llm=model)
    plugins.append({"id": "money", "name": "Money", "description": "Ledger"})
    await plugin_ideas.get_plugin_ideas("alice", llm=model)
    assert len(model.calls) == 2
    assert "Money: Ledger" in model.calls[-1][1]["content"]
    assert len(plugin_ideas._cache) == 1


@pytest.mark.asyncio
async def test_plugin_ideas_invalid_response_keeps_usable_fallback(monkeypatch):
    class Invalid:
        async def complete(self, messages, tools=None):
            return LLMResponse(content="wrong format", tool_calls=[])

    monkeypatch.setattr(
        "app.plugins.manager.get_manager",
        lambda: SimpleNamespace(
            list=lambda: [
                {"id": "money", "name": "Money", "description": "Ledger"},
            ]
        ),
    )
    result = await plugin_ideas.get_plugin_ideas("alice", llm=Invalid())
    assert result["source"] == "fallback"
    assert len(result["prompts"]) == 3
    assert all(
        p["key"] != "money" and "plugin" in p["prompt"] for p in result["prompts"]
    )
    assert not plugin_ideas._cache
