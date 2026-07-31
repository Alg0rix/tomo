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


def test_use_skill_unknown_is_error() -> None:
    assert execute("use_skill", {"skill_id": "no_such_skill"}).startswith("Error")


def test_use_skill_missing_id_is_error() -> None:
    assert execute("use_skill", {}).startswith("Error")
