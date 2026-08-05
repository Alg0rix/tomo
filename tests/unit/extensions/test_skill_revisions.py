"""Slice 3 — skill revisions/vN.md on edit."""

from __future__ import annotations

from pathlib import Path

from app.extensions import skills as skills_mod


def test_edit_creates_revision(tmp_path: Path, monkeypatch) -> None:
    from app.core import config

    monkeypatch.setattr(config, "TOMO_HOME", tmp_path / "home")
    (tmp_path / "home").mkdir(parents=True)

    skills_mod.write_library_skill(
        skill_id="demo-skill",
        name="Demo Skill",
        description="A demo",
        body="Step one.",
        home_root=tmp_path / "home",
    )
    skills_mod.edit_library_skill(
        "demo-skill",
        body="Step one.\nStep two.",
        home_root=tmp_path / "home",
    )
    revs = skills_mod.list_skill_revisions("demo-skill", home_root=tmp_path / "home")
    assert len(revs) >= 1
    assert revs[0]["name"] == "v1.md"
    text = Path(revs[0]["path"]).read_text(encoding="utf-8")
    assert "Step one." in text

    skills_mod.patch_library_skill(
        "demo-skill",
        old_string="Step two.",
        new_string="Step two (patched).",
        home_root=tmp_path / "home",
    )
    revs2 = skills_mod.list_skill_revisions("demo-skill", home_root=tmp_path / "home")
    assert len(revs2) >= 2
