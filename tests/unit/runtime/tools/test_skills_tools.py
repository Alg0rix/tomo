"""list_skills / use_skill tool tests."""

from __future__ import annotations

import pytest

from app.runtime.tools.registry import execute, reset_registry
from app.services import store


@pytest.fixture(autouse=True)
def _reset(tmp_path) -> None:
    reset_registry()
    store.rebind(tmp_path / "skills_tools.db")
    yield
    reset_registry()


def test_list_skills_includes_seeded() -> None:
    result = execute("list_skills", {})
    assert "onboarding" in result or "Vendor" in result


def test_use_skill_returns_description() -> None:
    skills = store.list_skills()
    assert skills
    sid = skills[0]["id"]
    result = execute("use_skill", {"skill_id": sid})
    assert "Skill:" in result
    assert skills[0]["name"] in result


def test_use_skill_unknown_is_error() -> None:
    assert execute("use_skill", {"skill_id": "no_such_skill"}).startswith("Error")


def test_use_skill_missing_id_is_error() -> None:
    assert execute("use_skill", {}).startswith("Error")
