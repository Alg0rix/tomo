"""Skill package discovery, install, and SKILL.md loading.

Discovers agentskills.io-style packages (directory containing ``SKILL.md`` with
optional YAML frontmatter) from:

1. ``$TOMO_HOME/library/skills`` — managed install target (read/write)
2. External dirs from ``TOMO_SKILLS_EXTERNAL_DIRS`` (colon-separated). When the
   env var is **unset**, defaults to ``~/.agents/skills`` and ``~/.agent/skills``
   (common skill-folder conventions). Set the env var to empty to disable
   external discovery (tests do this).

Managed installs copy a skill tree into the library dir. External skills are
discovered read-only and never deleted by uninstall.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app.core import home

_EXCLUDED_DIR_NAMES = frozenset(
    {".git", ".github", ".hub", ".archive", "__pycache__", "node_modules", ".venv"}
)
_FRONTMATTER_END = re.compile(r"\n---\s*\n")


@dataclass(frozen=True)
class DiscoveredSkill:
    """One skill package on disk."""

    id: str
    name: str
    description: str
    version: str
    path: Path  # directory containing SKILL.md
    skill_md: Path
    source: str  # library | agents | agent | external
    body: str


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse optional YAML-ish frontmatter; no PyYAML dependency."""
    if not content.startswith("---"):
        return {}, content
    match = _FRONTMATTER_END.search(content[3:])
    if not match:
        return {}, content
    yaml_block = content[3 : match.start() + 3]
    body = content[match.end() + 3 :]
    meta: dict[str, Any] = {}
    # Simple key: value parser (handles folded `>` descriptions as single line).
    current_key: str | None = None
    current_fold: list[str] = []
    for line in yaml_block.splitlines():
        if current_key and (line.startswith("  ") or line.startswith("\t")):
            current_fold.append(line.strip())
            continue
        if current_key and current_fold:
            meta[current_key] = " ".join(current_fold).strip()
            current_key = None
            current_fold = []
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key or key.startswith("#"):
            continue
        if val in {">", "|"}:
            current_key = key
            current_fold = []
            continue
        meta[key] = val
    if current_key and current_fold:
        meta[current_key] = " ".join(current_fold).strip()
    return meta, body


def slugify_skill_id(raw: str) -> str:
    text = (raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = text.strip("-_")
    return text or "skill"


def external_skill_roots() -> list[Path]:
    """User-shared skill directories (read-only)."""
    raw = os.environ.get("TOMO_SKILLS_EXTERNAL_DIRS")
    if raw is None:
        candidates = [
            Path.home() / ".agents" / "skills",
            Path.home() / ".agent" / "skills",
        ]
    elif not raw.strip():
        return []
    else:
        candidates = [Path(p.strip()).expanduser() for p in raw.split(":") if p.strip()]
    out: list[Path] = []
    seen: set[Path] = set()
    for p in candidates:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if not resolved.is_dir() or resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def skill_search_roots(home_root: Path | None = None) -> list[tuple[Path, str]]:
    """Ordered ``(dir, source_label)`` roots to scan."""
    roots: list[tuple[Path, str]] = []
    lib = home.library_skills_dir(home_root)
    roots.append((lib, "library"))
    for ext in external_skill_roots():
        label = "agents" if ext.name == "skills" and ext.parent.name == ".agents" else (
            "agent" if ext.name == "skills" and ext.parent.name == ".agent" else "external"
        )
        roots.append((ext, label))
    return roots


def iter_skill_md_files(root: Path) -> Iterator[Path]:
    """Yield ``SKILL.md`` paths under ``root`` (non-recursive category layouts OK)."""
    if not root.is_dir():
        return
    # Direct children: root/<skill>/SKILL.md
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.is_dir() or entry.name in _EXCLUDED_DIR_NAMES:
            continue
        skill_md = entry / "SKILL.md"
        if skill_md.is_file():
            yield skill_md
            continue
        # One nesting level: root/<category>/<skill>/SKILL.md
        try:
            subdirs = list(entry.iterdir())
        except OSError:
            continue
        for sub in subdirs:
            if not sub.is_dir() or sub.name in _EXCLUDED_DIR_NAMES:
                continue
            nested = sub / "SKILL.md"
            if nested.is_file():
                yield nested


def load_discovered_skill(
    skill_md: Path, source: str, *, root: Path | None = None
) -> DiscoveredSkill | None:
    from app.core.paths import try_under

    try:
        md = skill_md
        if root is not None:
            jailed = try_under(root, skill_md)
            if jailed is None:
                return None
            md = jailed
        else:
            md = skill_md.resolve()
        text = md.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return None
    meta, body = parse_frontmatter(text)
    dirname = md.parent.name
    sid = slugify_skill_id(str(meta.get("name") or dirname))
    name = str(meta.get("name") or dirname).strip() or sid
    description = str(meta.get("description") or "").strip()
    if not description:
        # First non-empty body line as fallback blurb
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line[:240]
                break
    version = str(meta.get("version") or "1.0").strip() or "1.0"
    return DiscoveredSkill(
        id=sid,
        name=name,
        description=description,
        version=version,
        path=md.parent,
        skill_md=md,
        source=source,
        body=body.strip(),
    )


def discover_skills(home_root: Path | None = None) -> list[DiscoveredSkill]:
    """Scan all roots; library wins on id collisions, then first external."""
    by_id: dict[str, DiscoveredSkill] = {}
    for root, source in skill_search_roots(home_root):
        for skill_md in iter_skill_md_files(root):
            skill = load_discovered_skill(skill_md, source, root=root)
            if skill is None:
                continue
            existing = by_id.get(skill.id)
            if existing is None:
                by_id[skill.id] = skill
            elif existing.source != "library" and source == "library":
                by_id[skill.id] = skill
    return sorted(by_id.values(), key=lambda s: s.name.lower())


def read_skill_body(skill_id: str, home_root: Path | None = None) -> str | None:
    """Return markdown body (no frontmatter) for ``skill_id``, or None."""
    sid = slugify_skill_id(skill_id)
    for skill in discover_skills(home_root):
        if skill.id == sid:
            return skill.body or skill.description
    return None


def install_from_path(
    src: Path,
    *,
    skill_id: str | None = None,
    home_root: Path | None = None,
) -> DiscoveredSkill:
    """Copy a skill directory (or parent of SKILL.md) into the library."""
    from app.core.paths import ensure_under

    src = Path(src).expanduser().resolve()
    if src.is_file() and src.name == "SKILL.md":
        src = src.parent
    if not src.is_dir():
        raise FileNotFoundError(f"skill path not found: {src}")
    skill_md = src / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"no SKILL.md in {src}")
    loaded = load_discovered_skill(skill_md, "library", root=src)
    if loaded is None:
        raise ValueError(f"could not parse skill at {src}")
    sid = slugify_skill_id(skill_id or loaded.id)
    dest_root = home.library_skills_dir(home_root).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = ensure_under(dest_root, sid)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, dirs_exist_ok=False)
    installed = load_discovered_skill(dest / "SKILL.md", "library", root=dest_root)
    if installed is None:
        raise RuntimeError(f"install failed for {sid}")
    # Re-key id to dest dirname when forced
    if installed.id != sid:
        installed = DiscoveredSkill(
            id=sid,
            name=installed.name,
            description=installed.description,
            version=installed.version,
            path=installed.path,
            skill_md=installed.skill_md,
            source="library",
            body=installed.body,
        )
    return installed


def uninstall_library_skill(skill_id: str, home_root: Path | None = None) -> bool:
    """Remove a managed library skill directory. External skills cannot be removed."""
    from app.core.paths import try_under

    sid = slugify_skill_id(skill_id)
    lib = home.library_skills_dir(home_root).resolve()
    dest = try_under(lib, sid)
    if dest is None or not dest.is_dir():
        return False
    shutil.rmtree(dest)
    return True


def sync_skills_to_db(conn: Any, home_root: Path | None = None) -> list[dict[str, Any]]:
    """Upsert discovered skills into SQLite; drop stale disk-backed rows.

    Keeps manually seeded rows that have empty ``path`` only if they are not
    shadowed by a discovered id. Prefer disk as source of truth for ids that
    exist on disk.
    """
    import sqlite3

    assert isinstance(conn, sqlite3.Connection)
    discovered = discover_skills(home_root)
    now = time.time()
    disk_ids = {s.id for s in discovered}

    # Ensure path/source columns exist (migrate may have run already).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(skills)")}
    if "path" not in cols:
        conn.execute("ALTER TABLE skills ADD COLUMN path TEXT NOT NULL DEFAULT ''")
    if "source" not in cols:
        conn.execute("ALTER TABLE skills ADD COLUMN source TEXT NOT NULL DEFAULT ''")

    for skill in discovered:
        conn.execute(
            "INSERT INTO skills (id, name, description, version, enabled, tool_count, "
            "created_at, path, source) VALUES (?,?,?,?,1,0,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
            "description=excluded.description, version=excluded.version, "
            "path=excluded.path, source=excluded.source",
            (
                skill.id,
                skill.name,
                skill.description,
                skill.version,
                now,
                str(skill.skill_md),
                skill.source,
            ),
        )

    # Remove previously synced disk skills that disappeared (keep empty-path seeds).
    for row in conn.execute(
        "SELECT id, path, source FROM skills WHERE path != '' OR source IN "
        "('library','agents','agent','external')"
    ).fetchall():
        if row["id"] not in disk_ids and (row["path"] or row["source"]):
            # Don't delete if it's a pure catalog seed with no path
            if row["path"]:
                conn.execute("DELETE FROM agent_skills WHERE skill_id=?", (row["id"],))
                conn.execute("DELETE FROM skills WHERE id=?", (row["id"],))

    conn.commit()
    rows = conn.execute("SELECT * FROM skills ORDER BY name ASC").fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "version": r["version"],
            "enabled": bool(r["enabled"]),
            "tool_count": int(r["tool_count"] or 0),
            "path": r["path"] if "path" in r.keys() else "",
            "source": r["source"] if "source" in r.keys() else "",
            "created_at": r["created_at"],
        }
        for r in rows
    ]


_MAX_SKILL_CHARS = 48_000
_ALLOWED_SUPPORT_DIRS = frozenset({"references", "templates", "scripts", "assets"})


def _library_skill_dir(skill_id: str, home_root: Path | None = None) -> Path:
    from app.core.paths import ensure_under

    lib = home.library_skills_dir(home_root).resolve()
    return ensure_under(lib, slugify_skill_id(skill_id))


def _compose_skill_md(
    *,
    name: str,
    description: str,
    body: str,
    version: str = "1.0",
    extra_meta: dict[str, str] | None = None,
) -> str:
    meta_lines = [
        f"name: {name.strip()}",
        f"description: {description.strip()}",
        f"version: {version.strip() or '1.0'}",
    ]
    if extra_meta:
        for key, val in extra_meta.items():
            if key and val is not None and str(val).strip():
                meta_lines.append(f"{key}: {str(val).strip()}")
    return "---\n" + "\n".join(meta_lines) + "\n---\n\n" + (body or "").strip() + "\n"


def write_library_skill(
    *,
    skill_id: str,
    name: str,
    description: str,
    body: str,
    version: str = "1.0",
    extra_meta: dict[str, str] | None = None,
    home_root: Path | None = None,
    overwrite: bool = False,
) -> DiscoveredSkill:
    """Create or overwrite a managed library skill (SKILL.md)."""
    sid = slugify_skill_id(skill_id or name)
    if not name.strip():
        raise ValueError("name is required")
    if not description.strip():
        raise ValueError("description is required")
    if not (body or "").strip():
        raise ValueError("body is required")
    content = _compose_skill_md(
        name=name,
        description=description,
        body=body,
        version=version,
        extra_meta=extra_meta,
    )
    if len(content) > _MAX_SKILL_CHARS:
        raise ValueError(f"SKILL.md exceeds {_MAX_SKILL_CHARS} characters")
    dest = _library_skill_dir(sid, home_root)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"skill already exists: {sid}")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(content, encoding="utf-8")
    loaded = load_discovered_skill(dest / "SKILL.md", "library", root=home.library_skills_dir(home_root))
    if loaded is None:
        raise RuntimeError(f"failed to load written skill {sid}")
    return loaded


def edit_library_skill(
    skill_id: str,
    *,
    content: str | None = None,
    name: str | None = None,
    description: str | None = None,
    body: str | None = None,
    version: str | None = None,
    home_root: Path | None = None,
) -> DiscoveredSkill:
    """Replace SKILL.md for an existing library skill."""
    sid = slugify_skill_id(skill_id)
    dest = _library_skill_dir(sid, home_root)
    skill_md = dest / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"library skill not found: {sid}")
    if content is not None:
        text = content
        if not text.strip().startswith("---"):
            raise ValueError("content must include YAML frontmatter (--- ... ---)")
        if len(text) > _MAX_SKILL_CHARS:
            raise ValueError(f"SKILL.md exceeds {_MAX_SKILL_CHARS} characters")
        skill_md.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    else:
        existing = load_discovered_skill(skill_md, "library", root=home.library_skills_dir(home_root))
        if existing is None:
            raise ValueError(f"could not parse skill {sid}")
        meta, old_body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        text = _compose_skill_md(
            name=name or existing.name,
            description=description or existing.description,
            body=body if body is not None else old_body,
            version=version or existing.version,
            extra_meta={
                k: str(v)
                for k, v in meta.items()
                if k not in {"name", "description", "version"}
            },
        )
        skill_md.write_text(text, encoding="utf-8")
    loaded = load_discovered_skill(skill_md, "library", root=home.library_skills_dir(home_root))
    if loaded is None:
        raise RuntimeError(f"failed to reload skill {sid}")
    return loaded


def patch_library_skill(
    skill_id: str,
    *,
    old_string: str,
    new_string: str,
    file_path: str = "SKILL.md",
    home_root: Path | None = None,
) -> DiscoveredSkill:
    """Find-and-replace within SKILL.md or an allowed support file."""
    if not old_string:
        raise ValueError("old_string is required")
    sid = slugify_skill_id(skill_id)
    dest = _library_skill_dir(sid, home_root)
    if not dest.is_dir():
        raise FileNotFoundError(f"library skill not found: {sid}")
    rel = (file_path or "SKILL.md").strip().lstrip("/")
    if ".." in Path(rel).parts:
        raise ValueError("path traversal is not allowed")
    target = (dest / rel).resolve()
    try:
        target.relative_to(dest.resolve())
    except ValueError as exc:
        raise ValueError("file must stay inside the skill directory") from exc
    if rel != "SKILL.md" and Path(rel).parts[0] not in _ALLOWED_SUPPORT_DIRS:
        raise ValueError(
            f"support files must be under {sorted(_ALLOWED_SUPPORT_DIRS)}"
        )
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {rel}")
    text = target.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        raise ValueError("old_string not found")
    if count > 1:
        raise ValueError(f"old_string matched {count} times; make it unique")
    updated = text.replace(old_string, new_string, 1)
    if len(updated) > _MAX_SKILL_CHARS:
        raise ValueError(f"result exceeds {_MAX_SKILL_CHARS} characters")
    target.write_text(updated, encoding="utf-8")
    loaded = load_discovered_skill(dest / "SKILL.md", "library", root=home.library_skills_dir(home_root))
    if loaded is None:
        raise RuntimeError(f"failed to reload skill {sid}")
    return loaded


def write_skill_support_file(
    skill_id: str,
    *,
    file_path: str,
    content: str,
    home_root: Path | None = None,
) -> Path:
    """Write a support file under references/templates/scripts/assets."""
    sid = slugify_skill_id(skill_id)
    dest = _library_skill_dir(sid, home_root)
    if not dest.is_dir():
        raise FileNotFoundError(f"library skill not found: {sid}")
    rel = (file_path or "").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError("invalid file_path")
    if Path(rel).parts[0] not in _ALLOWED_SUPPORT_DIRS:
        raise ValueError(
            f"file_path must start with one of {sorted(_ALLOWED_SUPPORT_DIRS)}"
        )
    target = (dest / rel).resolve()
    try:
        target.relative_to(dest.resolve())
    except ValueError as exc:
        raise ValueError("file must stay inside the skill directory") from exc
    if len(content or "") > _MAX_SKILL_CHARS:
        raise ValueError(f"content exceeds {_MAX_SKILL_CHARS} characters")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return target


def delete_library_skill(skill_id: str, home_root: Path | None = None) -> bool:
    """Delete a managed library skill directory."""
    return uninstall_library_skill(skill_id, home_root)


__all__ = [
    "DiscoveredSkill",
    "parse_frontmatter",
    "slugify_skill_id",
    "external_skill_roots",
    "skill_search_roots",
    "discover_skills",
    "read_skill_body",
    "install_from_path",
    "uninstall_library_skill",
    "sync_skills_to_db",
    "write_library_skill",
    "edit_library_skill",
    "patch_library_skill",
    "write_skill_support_file",
    "delete_library_skill",
]
