"""todo tool tests."""

from __future__ import annotations

import pytest

from app.core import home
from app.runtime.tools import sandbox
from app.runtime.tools.registry import execute, reset_registry


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch) -> None:
    reset_registry()
    monkeypatch.setenv("TOMO_HOME", str(tmp_path / "home"))
    # Refresh config.TOMO_HOME if cached — home helpers read config each call.
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "home")
    home.ensure_tomo_home()
    sandbox.reset_agent()
    yield
    sandbox.reset_agent()
    reset_registry()


def test_todo_add_list_complete() -> None:
    sandbox.bind_agent("main")
    added = execute("todo", {"action": "add", "content": "ship tools"})
    assert added.startswith("Added")
    todo_id = added.split(":", 1)[0].replace("Added ", "").strip()
    listed = execute("todo", {"action": "list"})
    assert todo_id in listed
    assert "ship tools" in listed
    done = execute("todo", {"action": "complete", "id": todo_id})
    assert "Completed" in done
    listed2 = execute("todo", {"action": "list"})
    assert "[x]" in listed2


def test_todo_complete_unknown_is_error() -> None:
    assert execute("todo", {"action": "complete", "id": "todo_missing"}).startswith(
        "Error"
    )


def test_todo_add_requires_content() -> None:
    assert execute("todo", {"action": "add"}).startswith("Error")
