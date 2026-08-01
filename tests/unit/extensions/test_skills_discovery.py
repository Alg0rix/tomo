"""Filesystem skill discovery + install tests."""

from __future__ import annotations

from pathlib import Path

from app.core import config, home
from app.extensions import skills as skills_ext
from app.runtime.tools.registry import execute, reset_registry
from app.services import store


def _write_skill(root: Path, skill_id: str, *, name: str | None = None, body: str = "Do the thing.") -> Path:
    d = root / skill_id
    d.mkdir(parents=True, exist_ok=True)
    title = name or skill_id
    (d / "SKILL.md").write_text(
        f"---\nname: {title}\ndescription: A test skill\nversion: 1.2.3\n---\n\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    return d


def test_parse_frontmatter_basic() -> None:
    meta, body = skills_ext.parse_frontmatter(
        "---\nname: demo\ndescription: hi\n---\n\nBody here\n"
    )
    assert meta["name"] == "demo"
    assert "Body here" in body


def test_discover_and_sync_library(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SKILLS_EXTERNAL_DIRS", "")
    store.rebind(tmp_path / "skills.db")
    lib = home.library_skills_dir(config.TOMO_HOME)
    _write_skill(lib, "demo-pack", name="Demo Pack", body="Use carefully.")
    synced = store.sync_skills()
    ids = {s["id"] for s in synced}
    assert "demo-pack" in ids
    skill = store.get_skill("demo-pack")
    assert skill is not None
    assert skill["source"] == "library"
    assert "Demo Pack" in skill["name"]


def test_install_from_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SKILLS_EXTERNAL_DIRS", "")
    store.rebind(tmp_path / "install.db")
    src = _write_skill(tmp_path / "src", "from-disk", name="From Disk")
    skill = store.install_skill_from_path(src)
    assert skill["id"] == "from-disk"
    assert (home.library_skills_dir(config.TOMO_HOME) / "from-disk" / "SKILL.md").is_file()
    assert store.uninstall_library_skill("from-disk") is True
    assert store.get_skill("from-disk") is None


def test_use_skill_returns_body(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SKILLS_EXTERNAL_DIRS", "")
    reset_registry()
    store.rebind(tmp_path / "use.db")
    _write_skill(
        home.library_skills_dir(config.TOMO_HOME),
        "brief",
        name="Brief",
        body="Always cite sources.",
    )
    store.sync_skills()
    result = execute("use_skill", {"skill_id": "brief"})
    assert "Always cite sources." in result
    assert "Brief" in result
    listed = execute("list_skills", {})
    assert "brief" in listed
    reset_registry()


def test_discover_external_agents_dir(tmp_path, monkeypatch) -> None:
    ext = tmp_path / "agents-skills"
    _write_skill(ext, "caveman-lite", name="caveman-lite")
    monkeypatch.setenv("TOMO_SKILLS_EXTERNAL_DIRS", str(ext))
    store.rebind(tmp_path / "ext.db")
    store.sync_skills()
    skill = store.get_skill("caveman-lite")
    assert skill is not None
    assert skill["source"] in {"external", "agents", "agent"}


def test_use_skill_reads_support_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOMO_SKILLS_EXTERNAL_DIRS", "")
    reset_registry()
    store.rebind(tmp_path / "use-file.db")
    d = _write_skill(
        home.library_skills_dir(config.TOMO_HOME),
        "with-refs",
        name="With Refs",
    )
    (d / "references").mkdir(parents=True, exist_ok=True)
    (d / "references" / "structure.md").write_text(
        "# Structure\n\nPick a rhythm.\n", encoding="utf-8"
    )
    store.sync_skills()
    result = execute("use_skill", {"skill_id": "with-refs", "file": "references/structure.md"})
    assert "Pick a rhythm." in result
    assert "structure.md" in result
    blocked = execute("use_skill", {"skill_id": "with-refs", "file": "../secrets.txt"})
    assert blocked.startswith("Error:")
    reset_registry()


def test_default_external_roots_include_tomo(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TOMO_SKILLS_EXTERNAL_DIRS", raising=False)
    fake_home = tmp_path / "home"
    skill_dir = fake_home / ".tomo" / "skills" / "tomo-only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tomo-only\ndescription: from .tomo\n---\n\nHi\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills_ext.Path, "home", lambda: fake_home)
    roots = skills_ext.external_skill_roots()
    assert any(r.name == "skills" and r.parent.name == ".tomo" for r in roots)
    labeled = skills_ext.skill_search_roots(tmp_path / "tomo-home")
    sources = {src for _, src in labeled}
    assert "tomo" in sources
