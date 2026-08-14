"""list_skills / use_skill tool tests."""

from __future__ import annotations

import re

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
