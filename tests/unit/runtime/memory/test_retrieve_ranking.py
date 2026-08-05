"""retrieve_for_turn prefers user + project + high-confidence semantic."""

from __future__ import annotations

from pathlib import Path

from app.runtime.memory.retrieve import retrieve_for_turn
from app.services import store


def test_retrieve_orders_user_project_semantic(tmp_path: Path, monkeypatch) -> None:
    store.rebind(tmp_path / "ret.db")
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "home")
    (tmp_path / "home" / "memories").mkdir(parents=True)
    (tmp_path / "home" / "memories" / "USER.md").write_text(
        "# User\n\nPrefers short answers\n", encoding="utf-8"
    )

    store.create_agent(
        {
            "id": "dev",
            "name": "Dev",
            "workplace_id": "wp1",
        }
    )
    from app.runtime.memory import project as project_mem

    project_mem.add_entry("wp1", "Stack is FastAPI + SQLite", home_root=tmp_path / "home")

    store.create_knowledge_entry(
        {
            "id": "kb_ret",
            "title": "Widget deploy notes",
            "body": "Deploy widgets with care",
            "confidence": 0.92,
        }
    )

    block = retrieve_for_turn(
        "widget deploy preferences", agent_id="dev", limit=4
    )
    assert "User prefs [user]" in block
    assert "Prefers short answers" in block
    assert "Project notes [project]" in block
    assert "FastAPI" in block
    assert "Knowledge [semantic]" in block
    assert "Widget deploy" in block
    # User section appears before knowledge.
    assert block.index("User prefs") < block.index("Knowledge [semantic]")
    assert block.index("Project notes") < block.index("Knowledge [semantic]")
