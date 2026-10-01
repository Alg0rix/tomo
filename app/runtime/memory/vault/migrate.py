"""One-way upgrade of retired note files into authoritative vault pages.

Sources are deleted only after exact content verification. Interrupted upgrades
can be rerun; runtime never falls back to the retired paths.
"""
from __future__ import annotations

import sqlite3
import fcntl
import os
import json
from pathlib import Path
from app.core import home
from . import doc, paths, write
from .notes import facts, scoped_key


def migrate_notes(conn: sqlite3.Connection, *, home_root: Path | None = None) -> int:
    """Serialize upgrades across workers sharing the same Tomo home."""
    root = home._root(home_root)
    state = root / 'state'
    if state.is_symlink() or not state.resolve().is_relative_to(root.resolve()):
        raise ValueError('unsafe migration lock directory')
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state / 'vault-migration.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return _upgrade(conn, home_root=home_root)
    finally:
        os.close(fd)


def _upgrade(conn: sqlite3.Connection, *, home_root: Path | None = None) -> int:
    root = home._root(home_root)
    sources: list[tuple[Path, list[str], str]] = []
    sources.append((root / 'memories' / 'USER.md', ['web'], 'user/profile'))
    for path in (root / 'memories' / 'users').glob('*/USER.md'):
        sources.append((path, [path.parent.name], 'user/profile'))
    for path in (root / 'agents').glob('*/MEMORY.md'):
        sources.append((path, ['web'], scoped_key('agent', path.parent.name)))
    for path in (root / 'agents').glob('*/users/*/MEMORY.md'):
        sources.append((path, [path.parent.name], scoped_key('agent', path.parents[2].name)))
    for path in (root / 'workplaces').glob('*/PROJECT.md'):
        # The old workplace notes were shared; migrate to its participants.
        wid = path.parent.name
        users = [r[0] for r in conn.execute(
            'SELECT DISTINCT s.user_id FROM sessions s LEFT JOIN session_agents sa ON sa.session_id=s.id LEFT JOIN agents a ON a.id=sa.agent_id WHERE s.workplace_id=? OR a.workplace_id=?', (wid, wid))]
        sources.append((path, sorted(set(['web', *users])), scoped_key('project', wid)))
    for directory in [root / 'library' / 'memory', *(root / 'agents').glob('*/knowledge')]:
        for path in directory.rglob('*.md'):
            sources.append((path, ['web'], scoped_key('topic', str(path.relative_to(root)))))
    count = 0
    for source, users, key in sources:
        if not source.exists():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError('unsafe memory migration source')
        raw = source.read_text(encoding='utf-8')
        parts = raw.split('\n§\n') if source.name in {'USER.md', 'MEMORY.md', 'PROJECT.md'} else [raw]
        entries = [e.strip() for e in parts if e.strip()]
        for uid in users:
            path = paths.entity_path(uid, key, home_root=home_root)
            with write._lock(uid):
                page = doc.parse(path.read_text(encoding='utf-8')) if path.is_file() else doc.Document(
                    {'type': key.split('/')[0], 'aliases': [], 'tags': []},
                    '# ' + ('User profile' if key == 'user/profile' else source.parent.name + ' notes'))
                live = facts(uid, key, home_root=home_root)
                missing = [entry for entry in entries if entry not in live]
                if missing:
                    write._body(page, page.entries + [doc.encode_fact(entry) + ' (origin: user)' for entry in missing])
                    write._save(uid, path, page, home_root, conn)
            saved = facts(uid, key, home_root=home_root)
            if any(e not in saved for e in entries):
                raise RuntimeError(f'memory migration verification failed: {source}')
        if source.read_text(encoding='utf-8') != raw:
            raise RuntimeError(f'memory source changed during migration: {source}')
        source.unlink()
        count += 1
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'knowledge_entries' in tables:
        snapshot = [dict(row) for row in conn.execute('SELECT * FROM knowledge_entries ORDER BY id').fetchall()]
        for data in snapshot:
            uid = data.get('user_id') or 'web'
            key = scoped_key('topic', 'knowledge:' + data['id'])
            path = paths.entity_path(uid, key, home_root=home_root)
            # Import deterministic pages once, preserving title, tags and source metadata.
            body = data.get('body') or ''
            encoded = doc.encode_fact(body)
            page = doc.Document({'type': 'topic', 'aliases': [data['id']],
                'tags': json.loads(data.get('tags_json') or '[]'),
                'updated': '', 'title': ' '.join(data['title'].split()),
                'import_id': data['id'], 'confidence': str(data.get('confidence', 0.7)),
                'use_count': str(data.get('use_count', 0)),
                'success_count': str(data.get('success_count', 0)),
                'created_at': str(data.get('created_at', 0)),
                'updated_at': str(data.get('updated_at', 0))},
                '# ' + ' '.join(data['title'].split()) + ('\n§ ' + encoded + ' (origin: user)' if body.strip() else ''))
            if not path.exists():
                write.atomic_write(path, doc.serialize(page))
            saved = doc.parse(path.read_text(encoding='utf-8'))
            if (saved.meta != page.meta or
                    [doc.fact_data(e)['text'] for e in saved.entries] != ([data['body'].strip()] if (data.get('body') or '').strip() else [])):
                raise RuntimeError(f'KB migration verification failed: {data["id"]}')
            from . import index
            index.reindex_file(conn, uid, path, home_root=home_root)
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        current = [dict(row) for row in conn.execute('SELECT * FROM knowledge_entries ORDER BY id').fetchall()]
        if current != snapshot:
            conn.rollback()
            raise RuntimeError('knowledge changed during migration; retry with writers stopped')
        count += len(snapshot)
        conn.execute('DROP TABLE IF EXISTS knowledge_fts')
        conn.execute('DROP TABLE knowledge_entries')
        if 'memory_embeddings' in tables:
            conn.execute("DELETE FROM memory_embeddings WHERE scope='knowledge'")
    # Replace retired allowlist entries rather than retaining compatibility tools.
    conn.execute("INSERT OR IGNORE INTO agent_tools(agent_id,tool_id,enabled) SELECT agent_id,'memory',MAX(enabled) FROM agent_tools WHERE tool_id IN ('remember','recall','forget_memory') GROUP BY agent_id")
    conn.execute("DELETE FROM agent_tools WHERE tool_id IN ('remember','recall','forget_memory')")
    conn.commit()
    return count
