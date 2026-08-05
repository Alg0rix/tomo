"""Slice 3 — swarm_notes shared lane."""

from __future__ import annotations

from pathlib import Path

from app.services import store


def test_swarm_notes_insert_list(tmp_path: Path) -> None:
    store.rebind(tmp_path / "swarm.db")
    note = store.insert_swarm_note(
        session_id="s1",
        from_agent_id="coord",
        to_agent_id="dev",
        delegate_call_id="c1",
        reason="investigate flaky test",
        content="Found race in fixture teardown.",
        status="ok",
    )
    assert note is not None
    assert note["to_agent_id"] == "dev"
    rows = store.list_swarm_notes(session_id="s1")
    assert len(rows) == 1
    assert "race" in rows[0]["content"]
    assert store.list_swarm_notes(session_id="other") == []
