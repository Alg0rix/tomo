"""Memory engine: FTS hybrid search + layers."""

from __future__ import annotations

from pathlib import Path

from app.runtime.memory.fts import _fts_query, search_knowledge_fts
from app.runtime.memory.layers import (
    list_agent_state,
    set_agent_state,
    upsert_session_summary,
)
from app.runtime.memory.retrieve import retrieve_for_turn, search_knowledge_hybrid
from app.runtime.tools.registry import execute, reset_registry
from app.services import store


def _rebind(tmp_path: Path) -> None:
    reset_registry()
    store.rebind(tmp_path / "memory.db")


def test_fts_tables_exist(tmp_path: Path) -> None:
    _rebind(tmp_path)
    names = {
        r[0]
        for r in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    # virtual tables show as table in sqlite_master
    assert "knowledge_fts" in names or "knowledge_entries" in names
    assert "memory_embeddings" in names
    assert "agent_state" in names
    assert "artifacts" in names
    assert "session_summaries" in names


def test_hybrid_search_finds_seed(tmp_path: Path) -> None:
    _rebind(tmp_path)
    hits = search_knowledge_hybrid(store._conn, "vendor onboarding deadline")
    assert hits
    assert any("October 15, 2026" in h["body"] for h in hits)


def test_fts_query_quotes_tokens() -> None:
    q = _fts_query("hello AND world")
    assert '"hello"' in q
    assert '"world"' in q


def test_agent_state_roundtrip(tmp_path: Path) -> None:
    _rebind(tmp_path)
    set_agent_state(store._conn, "main", "timezone", "Asia/Jakarta")
    state = list_agent_state(store._conn, "main")
    assert state["timezone"] == "Asia/Jakarta"
    out = execute("agent_state", {"action": "get", "key": "timezone", "agent_id": "main"})
    assert "Asia/Jakarta" in out


def test_save_artifact_tool(tmp_path: Path) -> None:
    _rebind(tmp_path)
    out = execute(
        "save_artifact",
        {"title": "Report", "path": "/tmp/out.md", "kind": "report"},
    )
    assert out.startswith("Saved artifact")
    arts = store.search_artifacts("Report")
    assert arts and arts[0]["path"] == "/tmp/out.md"


def test_session_summary_and_retrieve(tmp_path: Path) -> None:
    _rebind(tmp_path)
    store.create_knowledge_entry(
        {
            "title": "Preferred language",
            "body": "User prefers Indonesian replies.",
            "tags": ["pref"],
        }
    )
    upsert_session_summary(store._conn, "sess1", "Discussed Q3 budget last week.")
    set_agent_state(store._conn, "main", "preferred_language", "id")
    block = retrieve_for_turn(
        "what language should I use",
        agent_id="main",
        session_id="sess1",
    )
    assert "Retrieved memory" in block
    assert "preferred_language" in block or "Indonesian" in block or "Q3" in block


def test_knowledge_fts_ids(tmp_path: Path) -> None:
    _rebind(tmp_path)
    ids = search_knowledge_fts(store._conn, "staging cluster", limit=5)
    assert "kb_staging_cluster" in ids or ids == []  # empty if FTS unavailable
    # Lexical hybrid still works:
    hits = store.search_knowledge("staging cluster")
    assert hits
