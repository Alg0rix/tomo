from app.runtime.tools import start_swarm


def test_requires_root_chat_context():
    assert start_swarm.run({"request": "Research MCP", "consent_quote": "swarm"}).startswith("Error:")


def test_requires_current_user_consent_quote():
    token = start_swarm.bind_context("Research MCP")
    try:
        assert start_swarm.run({"request": "Research MCP", "consent_quote": "run a swarm"}).startswith("Error:")
        assert start_swarm.run({"request": "Research MCP", "consent_quote": ""}).startswith("Error:")
    finally:
        start_swarm.reset_context(token)


def test_valid_handoff_returns_task():
    token = start_swarm.bind_context("coba lu bikin swarm buat riset MCP")
    try:
        result = start_swarm.run({"request": "Research MCP architecture and tools", "consent_quote": "bikin swarm"})
        assert result == start_swarm.ACCEPTED
    finally:
        start_swarm.reset_context(token)


def test_handoff_cannot_be_called_by_nested_worker(monkeypatch):
    token = start_swarm.bind_context("run a swarm")
    monkeypatch.setattr("app.runtime.agent.subagent.current_depth", lambda: 1)
    try:
        assert start_swarm.run({"request": "MCP", "consent_quote": "run a swarm"}).startswith("Error:")
    finally:
        start_swarm.reset_context(token)
