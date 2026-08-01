"""todo tool tests — write/read + legacy actions."""

from __future__ import annotations

import json

import pytest

from app.core import home
from app.runtime.tools import sandbox
from app.runtime.tools import todo as todo_mod
from app.runtime.tools.registry import execute, reset_registry


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch) -> None:
    reset_registry()
    monkeypatch.setenv("TOMO_HOME", str(tmp_path / "home"))
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "home")
    home.ensure_tomo_home()
    sandbox.reset_agent()
    # Fresh session store per test.
    tok = todo_mod.bind_session(f"test-{tmp_path.name}")
    yield
    todo_mod.reset_session(tok)
    sandbox.reset_agent()
    reset_registry()


def test_todo_write_replace_and_read() -> None:
    out = execute(
        "todo",
        {
            "todos": [
                {"id": "1", "content": "explore", "status": "pending"},
                {"id": "2", "content": "edit", "status": "in_progress"},
            ]
        },
    )
    data = json.loads(out)
    assert data["summary"]["total"] == 2
    assert data["summary"]["in_progress"] == 1
    assert data["todos"][1]["status"] == "in_progress"

    listed = json.loads(execute("todo", {}))
    assert listed["summary"]["total"] == 2


def test_todo_merge_updates_by_id() -> None:
    execute(
        "todo",
        {
            "todos": [
                {"id": "1", "content": "explore", "status": "pending"},
                {"id": "2", "content": "edit", "status": "pending"},
            ]
        },
    )
    out = execute(
        "todo",
        {
            "merge": True,
            "todos": [{"id": "1", "status": "completed"}],
        },
    )
    data = json.loads(out)
    by_id = {t["id"]: t for t in data["todos"]}
    assert by_id["1"]["status"] == "completed"
    assert by_id["1"]["content"] == "explore"
    assert by_id["2"]["status"] == "pending"


def test_legacy_add_list_complete_still_works() -> None:
    added = execute("todo", {"action": "add", "content": "ship tools"})
    data = json.loads(added)
    assert data["summary"]["total"] == 1
    tid = data["todos"][0]["id"]
    done = execute("todo", {"action": "complete", "id": tid})
    assert json.loads(done)["todos"][0]["status"] == "completed"


def test_todo_complete_unknown_is_error() -> None:
    assert execute("todo", {"action": "complete", "id": "todo_missing"}).startswith(
        "Error"
    )


def test_todo_add_requires_content() -> None:
    assert execute("todo", {"action": "add"}).startswith("Error")


def test_seed_from_dag_maps_nodes() -> None:
    from app.runtime.agent.atg.graph import TaskDAG, TaskNode

    dag = TaskDAG("do stuff")
    dag.add_node(
        TaskNode(id="n1", goal="read file", tool="read_file", outputs=["result"])
    )
    dag.add_node(
        TaskNode(
            id="n2",
            goal="edit file",
            tool="str_replace",
            outputs=["result"],
            deps=["n1"],
        )
    )
    snap = todo_mod.seed_from_dag(dag, session_id=None)
    assert [t["id"] for t in snap["todos"]] == ["n1", "n2"]
    assert snap["todos"][0]["content"] == "read file"
    snap2 = todo_mod.mark_node("n1", "completed")
    assert snap2["todos"][0]["status"] == "completed"
