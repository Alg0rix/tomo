"""Safe per-account Markdown vault paths."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from app.core import home

TYPES = frozenset({'person', 'project', 'tool', 'place', 'org', 'topic', 'user', 'agent'})
_SAFE_ID = re.compile(r'^[A-Za-z0-9_.:@-]{1,128}$')
_SAFE_SLUG = re.compile(r'^[a-z0-9][a-z0-9_-]{0,127}$')


def vault_root(user_id: str, *, home_root: Path | None = None) -> Path:
    if not _SAFE_ID.fullmatch(user_id or '') or '..' in user_id:
        raise ValueError('invalid user id')
    root = home._root(home_root) / 'memory' / 'vault' / user_id
    if root.is_symlink():
        raise ValueError('invalid vault root')
    return root


def _guard(root: Path, path: Path) -> Path:
    if path.is_symlink():
        raise ValueError('symlink vault document')
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError('vault path escapes account root') from None
    return path


def entity_key(value: str) -> tuple[str, str]:
    parts = (value or '').split('/')
    if len(parts) != 2 or parts[0] not in TYPES or not _SAFE_SLUG.fullmatch(parts[1]):
        raise ValueError('entity must be type/slug with a safe slug')
    return parts[0], parts[1]


def entity_path(user_id: str, key: str, *, home_root: Path | None = None) -> Path:
    kind, slug = entity_key(key)
    root = vault_root(user_id, home_root=home_root)
    return _guard(root, root / 'entities' / kind / f'{slug}.md')


def timeline_path(user_id: str, day: str, *, home_root: Path | None = None) -> Path:
    parsed = date.fromisoformat(day)
    if parsed.isoformat() != day:
        raise ValueError('invalid day')
    root = vault_root(user_id, home_root=home_root)
    return _guard(root, root / 'timeline' / f'{parsed.year:04d}' / f'{parsed.month:02d}' / f'{day}.md')


def timeline_source(user_id: str, day: str, fragment: str = '', *, home_root: Path | None = None) -> str:
    path = timeline_path(user_id, day, home_root=home_root)
    key = f'{user_id}/{relative_doc(vault_root(user_id, home_root=home_root), path)}'
    return key + (f'#{fragment}' if fragment else '')


def relative_doc(root: Path, path: Path) -> str:
    resolved = path.resolve()
    rel = resolved.relative_to(root.resolve())
    if path.suffix != '.md' or path.is_symlink() or any(p.startswith('.') for p in rel.parts):
        raise ValueError('invalid vault document')
    return rel.as_posix()
