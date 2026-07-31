"""Platform entities in SQLite: skills, plugins, schedules (Slice G)."""

from __future__ import annotations

from pathlib import Path

from app.core import config, home
from app.services import store


def _rebind(tmp_path: Path) -> None:
    store.rebind(tmp_path / "platform.db")


def _install_demo_skills() -> None:
    lib = home.library_skills_dir(config.TOMO_HOME)
    for sid, desc in (
        ("onboarding", "Vendor intake"),
        ("deploy", "Deploy checklist"),
        ("research_brief", "Research notes"),
    ):
        d = lib / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {sid}\ndescription: {desc}\n---\n\n{desc}.\n",
            encoding="utf-8",
        )
    store.sync_skills()


def test_seed_skills_plugins_schedules(tmp_path: Path) -> None:
    _rebind(tmp_path)
    _install_demo_skills()
    skills = store.list_skills()
    assert {s["id"] for s in skills} >= {"onboarding", "deploy", "research_brief"}
    plugins = store.list_plugins()
    assert {p["id"] for p in plugins} >= {"kanban", "token_monitor", "connector"}
    schedules = store.list_schedules()
    assert {s["id"] for s in schedules} >= {"sch_001", "sch_002", "sch_003"}


def test_skills_plugins_schedules_survive_rebind(tmp_path: Path) -> None:
    db = tmp_path / "persist.db"
    store.rebind(db)
    _install_demo_skills()
    store.create_schedule(
        {
            "id": "sch_user",
            "name": "User job",
            "agent_id": "main",
            "interval_seconds": 120,
            "message": "ping",
            "enabled": True,
        }
    )
    store.update_plugin("kanban", {"enabled": False})
    store.set_agent_skills("ops", ["deploy", "onboarding"])

    store.rebind(db)
    store.sync_skills()

    sch = store.get_schedule("sch_user")
    assert sch is not None
    assert sch["name"] == "User job"
    assert sch["interval_seconds"] == 120
    assert store.get_plugin("kanban")["enabled"] is False
    assigned = {s["id"] for s in store.get_agent_skills("ops") if s["assigned"]}
    assert assigned == {"deploy", "onboarding"}
    assert store.get_skill("onboarding") is not None
    assert store.get_schedule("sch_001") is not None


def test_create_enable_disable_schedule(tmp_path: Path) -> None:
    _rebind(tmp_path)
    created = store.create_schedule(
        {
            "name": "Health ping",
            "agent_id": "ops",
            "interval_seconds": 30,
            "enabled": True,
        }
    )
    assert created["id"].startswith("sch_")
    assert created["enabled"] is True
    assert created["next_run"] is not None

    off = store.update_schedule(created["id"], {"enabled": False})
    assert off is not None
    assert off["enabled"] is False
    assert off["next_run"] is None

    on = store.update_schedule(created["id"], {"enabled": True})
    assert on is not None
    assert on["enabled"] is True
    assert on["next_run"] is not None


def test_agent_skills_start_unassigned(tmp_path: Path) -> None:
    _rebind(tmp_path)
    _install_demo_skills()
    main = store.get_agent_skills("main")
    assigned = {s["id"] for s in main if s["assigned"]}
    assert assigned == set()
