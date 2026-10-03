"""Consolidated tests (merged from: test_delegate.py, test_todo.py, test_skills_tools.py).
- test_delegate.py: Delegate tool: membership-safe handoff strings.
- test_todo.py: todo tool tests — write/read + legacy actions.
- test_skills_tools.py: list_skills / use_skill tool tests.
"""

from __future__ import annotations

import pytest
from app.runtime.tools import delegate as delegate_tool
from app.runtime.tools.registry import execute, get_openai_tools, reset_registry
import json
from app.core import home
from app.runtime.tools import sandbox
from app.runtime.tools import todo as todo_mod
import re
from app.services import store


# --- from test_delegate.py ---
@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_registry()
    yield
    reset_registry()


@pytest.fixture(autouse=True)
def _clear_delegate_ctx() -> None:
    delegate_tool.reset_context()
    yield
    delegate_tool.reset_context()


def test_delegate_schema_loaded() -> None:
    tools = get_openai_tools()
    schema = next(t for t in tools if t["function"]["name"] == "delegate")
    props = schema["function"]["parameters"]["properties"]
    assert "agent_id" in props or "name" in props or "agent" in props


def test_delegate_to_session_member_by_id() -> None:
    delegate_tool.bind_context(
        agent_ids=["main", "ops"],
        agents=[
            {"id": "main", "name": "Tomo"},
            {"id": "ops", "name": "Ops"},
        ],
    )
    result = execute("delegate", {"agent_id": "ops", "reason": "disk check"})
    assert result == "Delegated to ops"


def test_delegate_to_session_member_by_name() -> None:
    delegate_tool.bind_context(
        agent_ids=["main", "ops"],
        agents=[
            {"id": "main", "name": "Tomo"},
            {"id": "ops", "name": "Ops"},
        ],
    )
    result = execute("delegate", {"name": "Ops"})
    assert result == "Delegated to ops"


def test_delegate_rejects_non_member() -> None:
    delegate_tool.bind_context(
        agent_ids=["main", "ops"],
        agents=[
            {"id": "main", "name": "Tomo"},
            {"id": "ops", "name": "Ops"},
            {"id": "research", "name": "Research"},
        ],
    )
    result = execute("delegate", {"agent_id": "research"})
    assert result.startswith("Error:")
    assert "research" in result.lower() or "not" in result.lower()


def test_delegate_requires_target() -> None:
    delegate_tool.bind_context(agent_ids=["main"], agents=[{"id": "main", "name": "Tomo"}])
    result = execute("delegate", {})
    assert result.startswith("Error:")


def test_delegate_without_context_is_error() -> None:
    result = execute("delegate", {"agent_id": "ops"})
    assert result.startswith("Error:")


# --- from test_todo.py ---
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


# --- from test_skills_tools.py ---
@pytest.fixture(autouse=True)
def _reset_skills_tools(tmp_path) -> None:
    reset_registry()
    store.rebind(tmp_path / "skills_tools.db")
    yield
    reset_registry()


def test_list_skills_includes_seeded(tmp_path) -> None:
    # Catalog may be empty without disk packages; install one for the tool path.
    from app.core import config, home

    d = home.library_skills_dir(config.TOMO_HOME) / "onboarding"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: onboarding\ndescription: Vendor intake\n---\n\nSteps.\n",
        encoding="utf-8",
    )
    store.sync_skills()
    result = execute("list_skills", {})
    assert "onboarding" in result or "Vendor" in result


def test_list_skills_is_compact_and_paginated() -> None:
    from app.core import config, home

    skills_root = home.library_skills_dir(config.TOMO_HOME)
    long_tail = "FULL_DESCRIPTION_TAIL"
    for index in range(2):
        skill_dir = skills_root / f"catalog-{index}"
        skill_dir.mkdir(parents=True, exist_ok=True)
        description = f"Catalog summary {index}. " + ("detail " * 40) + long_tail
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: Catalog {index}\ndescription: {description}\n---\n\nBody.\n",
            encoding="utf-8",
        )
    store.sync_skills()

    first = execute("list_skills", {"query": "catalog-", "limit": 1})
    assert "catalog-0" in first
    assert long_tail not in first
    assert "Continue with offset=1" in first

    second = execute("list_skills", {"query": "catalog-", "limit": 1, "offset": 1})
    assert "catalog-1" in second
    assert "Continue with offset" not in second


def test_use_skill_returns_description() -> None:
    from app.core import config, home

    d = home.library_skills_dir(config.TOMO_HOME) / "demo-skill"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Demo\n---\n\nBody text.\n",
        encoding="utf-8",
    )
    store.sync_skills()
    skills = store.list_skills()
    assert skills
    sid = skills[0]["id"]
    result = execute("use_skill", {"skill_id": sid})
    assert "Skill:" in result
    assert skills[0]["name"] in result


def test_use_skill_paginates_large_body() -> None:
    from app.core import config, home

    skill_dir = home.library_skills_dir(config.TOMO_HOME) / "large-body"
    skill_dir.mkdir(parents=True, exist_ok=True)
    body = "BEGIN\n" + ("instruction " * 100) + "\nEND_BODY"
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: Large Body\ndescription: A large body\n---\n\n{body}",
        encoding="utf-8",
    )
    store.sync_skills()

    first = execute("use_skill", {"skill_id": "large-body", "limit": 120})
    assert "BEGIN" in first
    assert "END_BODY" not in first
    match = re.search(r"Continue with offset=(\d+)", first)
    assert match is not None

    page = first
    for _ in range(20):
        if "END_BODY" in page:
            break
        match = re.search(r"Continue with offset=(\d+)", page)
        assert match is not None
        page = execute(
            "use_skill",
            {"skill_id": "large-body", "limit": 120, "offset": int(match.group(1))},
        )
    assert "END_BODY" in page


def test_use_skill_unknown_is_error() -> None:
    assert execute("use_skill", {"skill_id": "no_such_skill"}).startswith("Error")


def test_use_skill_missing_id_is_error() -> None:
    assert execute("use_skill", {}).startswith("Error")


