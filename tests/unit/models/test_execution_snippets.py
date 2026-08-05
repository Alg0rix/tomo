"""Slice 3 — execution_snippets index."""

from __future__ import annotations

from pathlib import Path

from app.models.mixins import execution_snippets as ex
from app.services import store


def test_execution_snippet_search(tmp_path: Path) -> None:
    store.rebind(tmp_path / "exec.db")
    store.insert_execution_snippet(
        session_id="s1",
        agent_id="dev",
        source="artifact",
        title="pytest-out.txt",
        snippet="3 failed, race in teardown",
        tags=["execution", "artifact"],
    )
    hits = store.search_execution_snippets("race teardown", session_id="s1")
    assert hits
    assert "pytest" in hits[0]["title"]


def test_index_from_review_extract(tmp_path: Path) -> None:
    store.rebind(tmp_path / "exec2.db")

    def _run(conn):
        return ex.index_from_review_extract(
            conn,
            {
                "items": [
                    {
                        "tool": "save_artifact",
                        "type": "execution",
                        "kind": "write",
                        "saved_eligible": True,
                        "summary": "Saved artifact report.md",
                    },
                    {
                        "tool": "memory",
                        "type": "user",
                        "kind": "write",
                        "saved_eligible": True,
                        "summary": "pref",
                    },
                ]
            },
            session_id="s9",
            agent_id="a",
        )

    n = store.with_db(_run)
    assert n == 1
    hits = store.search_execution_snippets("report", session_id="s9")
    assert len(hits) == 1
