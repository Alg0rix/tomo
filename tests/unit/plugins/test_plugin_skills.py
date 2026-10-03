import json

import pytest

from app.extensions.skills import discover_skills, read_skill_body, read_skill_file
from app.plugins.manager import PluginManager
from app.runtime.tools.skills_tools import use_skill_run


@pytest.fixture
def package(tmp_path, monkeypatch):
    from app.plugins import manager as module

    path = tmp_path / "source"
    path.mkdir()
    (path / "tomo-plugin.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "name": "Demo",
                "version": "1",
                "sdk_version": 1,
            }
        )
    )
    (path / "plugin.py").write_text("def setup(api):\n    pass\n")
    skill = path / "skills" / "guide"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: guide\ndescription: Demo guide\n---\nOriginal instructions"
    )
    (skill / "references").mkdir()
    (skill / "references" / "usage.md").write_text("Usage reference")
    manager = PluginManager(tmp_path / "home")
    monkeypatch.setattr(module, "_manager", manager)
    manager.install(str(path))
    yield manager, path, skill
    manager.close()


def plugin_skills():
    return [s for s in discover_skills() if s.source.startswith("plugin:")]


def test_skill_lifecycle_and_reference_loading(package):
    manager, path, skill = package
    sid = "plugin__demo__guide"
    assert not plugin_skills()
    manager.change("demo", "enable")
    assert [s.id for s in plugin_skills()] == [sid]
    assert manager.list()[0]["skills"][0]["id"] == sid
    assert read_skill_body(sid) == "Original instructions"
    assert read_skill_file(sid, "references/usage.md") == "Usage reference"
    assert "Original instructions" in use_skill_run({"skill_id": sid})
    (skill / "SKILL.md").write_text("---\nname: guide\n---\nUpdated instructions")
    assert read_skill_body(sid) == "Original instructions"
    (path / "plugin.py").write_text('def setup(api):\n    raise ValueError("broken")\n')
    with pytest.raises(ValueError, match="broken"):
        manager.change("demo", "reload")
    assert read_skill_body(sid) == "Original instructions"
    (path / "plugin.py").write_text("def setup(api):\n    pass\n")
    manager.change("demo", "reload")
    assert read_skill_body(sid) == "Updated instructions"
    manager.change("demo", "disable")
    assert not plugin_skills()
    assert "unavailable" in use_skill_run({"skill_id": sid})
    manager.change("demo", "enable")
    manager.change("demo", "uninstall")
    assert read_skill_body(sid) is None


def test_offline_discovery_reads_metadata_without_executing(package):
    manager, path, _ = package
    manager.change("demo", "enable")
    (path / "plugin.py").write_text('raise RuntimeError("must not import")\n')
    offline = PluginManager(manager.root)
    assert offline.skill_packages()[0].id == "plugin__demo__guide"
    assert not offline.list()[0]["running"]


def test_skill_symlink_outside_package_rejected(package, tmp_path):
    manager, _, skill = package
    external = tmp_path / "outside.md"
    external.write_text("Outside instructions")
    (skill / "SKILL.md").unlink()
    (skill / "SKILL.md").symlink_to(external)
    with pytest.raises(ValueError, match="inside its package"):
        manager.change("demo", "enable")
    assert not plugin_skills()


def test_duplicate_skill_names_rejected(package):
    manager, path, _ = package
    other = path / "skills" / "other"
    other.mkdir()
    (other / "SKILL.md").write_text("---\nname: guide\n---\nDuplicate")
    with pytest.raises(ValueError, match="Duplicate plugin skill"):
        manager.change("demo", "enable")


def test_authoring_skills_and_references_are_discoverable():
    for sid, reference in [
        ("plugin-development", "references/sdk.md"),
        ("plugin-management", None),
        ("plugin-publishing", None),
    ]:
        assert read_skill_body(sid)
        if reference:
            assert "api.page" in read_skill_file(sid, reference)
