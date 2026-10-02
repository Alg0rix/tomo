"""session_search tool tests."""

from __future__ import annotations

import pytest

from app.runtime.tools.registry import execute, reset_registry
from app.services import store


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    reset_registry()
    store.rebind(tmp_path / "session_search.db")
    yield
    reset_registry()


def test_session_search_finds_content() -> None:
    sid = store.create_swarm_session(["main"], user_id="web")
    store.append_session_history(
        sid, {"type": "user", "content": "please deploy the staging stack"}
    )
    store.append_session_history(
        sid, {"type": "final", "content": "staging deploy started"}
    )
    result = execute("session_search", {"query": "staging"})
    assert "staging" in result.lower()
    assert sid in result


def test_session_search_no_match() -> None:
    sid = store.create_swarm_session(["main"], user_id="web")
    store.append_session_history(sid, {"type": "user", "content": "hello"})
    result = execute("session_search", {"query": "zzzz_nope"})
    assert result.startswith("No messages")


def test_session_search_empty_query_is_error() -> None:
    assert execute("session_search", {"query": ""}).startswith("Error")
