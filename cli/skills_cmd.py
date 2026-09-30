"""``tomo skills`` — list, sync, install, uninstall filesystem skills."""

from __future__ import annotations

import sys


def cmd_skills_list() -> int:
    from cli.config_cmd import local_db
    from app.models.mixins import skills as catalog
    from app.extensions.skills import sync_skills_to_db

    with local_db() as conn:
        sync_skills_to_db(conn)
        skills = catalog.list_skills(conn)
    if not skills:
        print("No skills found.")
        print(
            "  Install into ~/.tomo/library/skills or add packages under ~/.agents/skills / ~/.tomo/skills"
        )
        return 0
    for s in skills:
        flag = "on" if s.get("enabled") else "off"
        src = s.get("source") or "-"
        print(f"{s['id']:32} {flag:3} [{src:8}] {s.get('name')}")
    return 0


def cmd_skills_sync() -> int:
    from cli.config_cmd import local_db
    from app.extensions.skills import sync_skills_to_db

    with local_db() as conn:
        skills = sync_skills_to_db(conn)
    print(f"✓ Synced {len(skills)} skill(s)")
    return 0


def cmd_skills_install(path: str, skill_id: str | None = None) -> int:
    from cli.config_cmd import local_db
    from app.models.mixins import skills as catalog
    from app.extensions.skills import sync_skills_to_db

    try:
        from app.extensions.skills import install_from_path
        from pathlib import Path

        with local_db() as conn:
            installed = install_from_path(Path(path), skill_id=skill_id)
            sync_skills_to_db(conn)
            skill = catalog.get_skill(conn, installed.id)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"✗ Install failed: {exc}", file=sys.stderr)
        return 1
    print(f"✓ Installed {skill['id']} → {skill.get('path') or 'library'}")
    return 0


def cmd_skills_uninstall(skill_id: str) -> int:
    from cli.config_cmd import local_db
    from app.models.mixins import skills as catalog
    from app.extensions.skills import uninstall_library_skill

    with local_db() as conn:
        skill = catalog.get_skill(conn, skill_id)
        if uninstall_library_skill(skill_id):
            catalog.delete_skill(conn, skill_id)
            print(f"✓ Removed library skill {skill_id}")
            return 0
        if skill and skill.get("source") in {"agents", "agent", "external"}:
            print(
                f"✗ {skill_id} is an external skill ({skill.get('path')}). "
                "Remove it from its external directory, then use `tomo skills sync`.",
                file=sys.stderr,
            )
            return 1
        if catalog.delete_skill(conn, skill_id):
            print(f"✓ Removed catalog entry {skill_id}")
            return 0
    print(f"✗ Skill not found: {skill_id}", file=sys.stderr)
    return 1
