"""Slash skill activation expands into the LLM prompt, not chat UI text."""

from __future__ import annotations

import pytest

from app.core import config, home
from app.services import store
from app.services.chat import expand_slash_skill, expand_user_content_for_llm, resolve_slash_skill


@pytest.fixture(autouse=True)
def _db(tmp_path) -> None:
    store.rebind(tmp_path / "slash_skill.db")
    yield


def _install_demo() -> str:
    d = home.library_skills_dir(config.TOMO_HOME) / "hallmark"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: hallmark\ndescription: Design skill\n---\n\n"
        "Run the Hallmark design flow. Always ask Audience / Use case / Tone.\n",
        encoding="utf-8",
    )
    store.sync_skills()
    return "hallmark"


def test_resolve_slash_skill_known() -> None:
    _install_demo()
    hit = resolve_slash_skill("/hallmark audit this page")
    assert hit is not None
    skill, arg = hit
    assert skill["id"] == "hallmark"
    assert arg == "audit this page"


def test_expand_injects_skill_body() -> None:
    _install_demo()
    out = expand_slash_skill("/hallmark redesign the hero")
    assert "BEGIN SKILL" in out
    assert "Hallmark design flow" in out
    assert "redesign the hero" in out
    assert "Do not claim the skill is missing" in out


def test_unknown_slash_unchanged() -> None:
    assert expand_slash_skill("/not-a-real-skill please") == "/not-a-real-skill please"


def test_history_entry_expands_for_llm() -> None:
    _install_demo()
    text = expand_user_content_for_llm({"type": "user", "content": "/hallmark"})
    assert "BEGIN SKILL" in text
    assert "Ask Audience" in text or "Audience" in text


def test_expand_lists_support_file_hint() -> None:
    d = home.library_skills_dir(config.TOMO_HOME) / "hallmark"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: hallmark\ndescription: Design skill\n---\n\n"
        "Load references/structure.md before designing.\n",
        encoding="utf-8",
    )
    (d / "references").mkdir(exist_ok=True)
    (d / "references" / "structure.md").write_text("# Structure\n", encoding="utf-8")
    store.sync_skills()
    out = expand_slash_skill("/hallmark make a landing page")
    assert "use_skill" in out
    assert "references/structure.md" in out
    assert "read_file" in out
    assert "make a landing page" in out
