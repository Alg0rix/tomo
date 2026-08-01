"""``tomo skills`` — list, sync, install, uninstall filesystem skills."""

from __future__ import annotations

import sys


def cmd_skills_list() -> int:
    from app.services import store

    store.sync_skills()
    skills = store.list_skills()
    if not skills:
        print("No skills found.")
        print("  Install into ~/.tomo/library/skills or add packages under ~/.agents/skills")
        return 0
    for s in skills:
        flag = "on" if s.get("enabled") else "off"
        src = s.get("source") or "-"
        print(f"{s['id']:32} {flag:3} [{src:8}] {s.get('name')}")
    return 0


def cmd_skills_sync() -> int:
    from app.services import store

    skills = store.sync_skills()
    print(f"✓ Synced {len(skills)} skill(s)")
    return 0


def cmd_skills_install(path: str, skill_id: str | None = None) -> int:
    from app.services import store

    try:
        skill = store.install_skill_from_path(path, skill_id=skill_id)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"✗ Install failed: {exc}", file=sys.stderr)
        return 1
    print(f"✓ Installed {skill['id']} → {skill.get('path') or 'library'}")
    return 0


def cmd_skills_uninstall(skill_id: str) -> int:
    from app.services import store

    if store.uninstall_library_skill(skill_id):
        print(f"✓ Removed library skill {skill_id}")
        return 0
    # Fall back: drop catalog row only (external skills stay on disk)
    skill = store.get_skill(skill_id)
    if skill and skill.get("source") in {"agents", "agent", "external"}:
        print(
            f"✗ {skill_id} is an external skill ({skill.get('path')}). "
            "Remove it from ~/.agents/skills (or your external dir); "
            "use `tomo skills sync` after.",
            file=sys.stderr,
        )
        return 1
    if store.delete_skill(skill_id):
        print(f"✓ Removed catalog entry {skill_id}")
        return 0
    print(f"✗ Skill not found: {skill_id}", file=sys.stderr)
    return 1
