from app.runtime.tools import start_swarm

PLAN = {"tasks": [{"key": "review", "agent_id": "research", "brief": "Review MCP", "tools": []}]}


def test_requires_root_chat_context():
    assert start_swarm.run({"request": "Research MCP", "consent_quote": "swarm"}).startswith("Error:")


def test_handoff_without_separate_chat_consent():
    token = start_swarm.bind_context("Research MCP")
    try:
        assert start_swarm.run({"request": "Research MCP", "plan": PLAN}) == start_swarm.ACCEPTED
        assert start_swarm.run({"request": "Research MCP", "consent_quote": "", "plan": PLAN}) == start_swarm.ACCEPTED
        assert start_swarm.run({"request": "Research MCP; clarify answer: compare architecture and tools",
                                "consent_quote": "Proceed with both", "plan": PLAN}) == start_swarm.ACCEPTED
    finally:
        start_swarm.reset_context(token)


def test_handoff_requires_complete_task():
    token = start_swarm.bind_context("Research MCP")
    try:
        for request in (None, "", "  ", 42):
            assert start_swarm.run({"request": request}).startswith("Error:")
    finally:
        start_swarm.reset_context(token)


def test_valid_handoff_returns_task():
    token = start_swarm.bind_context("coba lu bikin swarm buat riset MCP")
    try:
        result = start_swarm.run({"request": "Research MCP architecture and tools", "consent_quote": "bikin swarm", "plan": PLAN})
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


def test_missing_plan_returns_feedback_without_creating_workers():
    from app.services.store import store
    token = start_swarm.bind_context("Test swarm lagi")
    try:
        before = store.with_db(lambda c: c.execute("SELECT COUNT(*) FROM swarm_agents").fetchone()[0])
        result = start_swarm.run({"request": "Test swarm lagi"})
        assert "plan workers in this main chat" in result
        assert store.with_db(lambda c: c.execute("SELECT COUNT(*) FROM swarm_agents").fetchone()[0]) == before
    finally:
        start_swarm.reset_context(token)
