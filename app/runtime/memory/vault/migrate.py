"""One-way upgrade of retired note files into authoritative vault pages.

Sources are deleted only after exact content verification. Interrupted upgrades
can be rerun; runtime never falls back to the retired paths.
"""
from __future__ import annotations

import sqlite3
import fcntl
import os
import json
import hashlib
import re
from pathlib import Path
from app.core import home
from . import doc, index, links, paths, write
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
        count = _upgrade(conn, home_root=home_root)
        for vault in sorted((root / 'memory/vault').glob('*')):
            if vault.is_dir():
                migrate_vault(conn, vault.name, home_root=home_root)
        return count
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


def _readable_key(conn, path: Path, page: doc.Document, root: Path) -> tuple[str, str]:
    """Recover known identities, not the preimage of an opaque SHA256 hash."""
    kind, digest = path.parent.name, path.stem[3:]
    identifiers = []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    table = {'agent': 'agents', 'project': 'workplaces'}.get(kind)
    directory = {'agent': 'agents', 'project': 'workplaces'}.get(kind)
    if table in tables:
        identifiers.extend(r[0] for r in conn.execute(f'SELECT id FROM {table}'))
    if directory:
        identifiers.extend(p.name for p in (root / directory).glob('*') if p.is_dir())
    if page.meta.get('import_id'):
        identifiers.append('knowledge:' + str(page.meta['import_id']))
    aliases = page.meta.get('aliases', [])
    if kind == 'topic' and isinstance(aliases, list):
        for prefix, namespace in [('From experience: ', 'experience:'), ('Procedure: ', 'procedure:')]:
            label = next((a[len(prefix):][:60] for a in aliases if a.startswith(prefix)), '')
            if label:
                return scoped_key(kind, namespace + label), label
    texts = [doc.fact_data(e)['text'] for e in page.entries]
    identifiers.extend(texts)
    identifier = next((s for s in identifiers if hashlib.sha256(s.encode()).hexdigest() == digest), None)
    title = next((line[2:].strip() for line in page.preamble.splitlines() if line.startswith('# ')), '')
    generated = not title or digest.casefold() in title.casefold()
    label = (identifier or next(iter(texts), 'memory')).splitlines()[0][:80] if generated else title
    if identifier is not None:
        return scoped_key(kind, identifier), label
    # Legacy library/file identities may no longer exist. Keep their old digest
    # as the suffix instead of inventing a different identity from mutable text.
    from app.models.ids import slugify
    return f'{kind}/{slugify(label)}-{digest[:8]}', label


def _merge_pages(target: doc.Document, source: doc.Document) -> doc.Document:
    for key, value in source.meta.items():
        if isinstance(value, list):
            target.meta[key] = list(dict.fromkeys([*target.meta.get(key, []), *value]))
        else:
            target.meta.setdefault(key, value)
    extras = '\n'.join(line for line in source.preamble.splitlines() if not line.startswith('# '))
    if extras.strip() and extras not in target.preamble:
        target.body = target.preamble + '\n' + extras + '\n' + '\n'.join('§ ' + e for e in target.entries)
    write._body(target, list(dict.fromkeys([*target.entries, *source.entries])))
    return target


def migrate_vault(conn: sqlite3.Connection, user_id: str, *, home_root: Path | None = None) -> dict:
    """Rename opaque pages and backfill mention links; preserve originals outside the vault.

    Rerunnable after interruption. Call with writers stopped (migrate_notes holds
    the process-wide migration file lock at startup); account writes share RLock.
    No cross-account references are rewritten or resolved.
    """
    root = home._root(home_root)
    vault = paths.vault_root(user_id, home_root=home_root)
    with write._lock(user_id):
        index.rebuild(conn, user_id, home_root=home_root)
        def resolved():
            return conn.execute('''SELECT count(*) FROM vault_links WHERE dst_resolved IS NOT NULL
                AND src IN (SELECT path FROM vault_docs WHERE user_id=?)''', (user_id,)).fetchone()[0]
        before = resolved()
        manual = {r['path']: set(links.manual_links(doc.parse(r['body'])))
                  for r in conn.execute('SELECT path,body FROM vault_docs WHERE user_id=?', (user_id,))}
        before_edges = [(r['src'], r['dst_resolved']) for r in conn.execute('''SELECT src,dst,dst_resolved FROM vault_links
            WHERE dst_resolved IS NOT NULL AND src IN (SELECT path FROM vault_docs WHERE user_id=?)''', (user_id,))
            if r['dst'] in manual[r['src']]]
        mappings, renamed = {}, 0
        pending = []
        for old in sorted(vault.glob('entities/*/id-*.md')):
            if not re.fullmatch(r'id-[a-f0-9]{64}', old.stem):
                continue
            old = paths.entity_path(user_id, f'{old.parent.name}/{old.stem}', home_root=home_root)
            raw = old.read_text(encoding='utf-8')
            page = doc.parse(raw)
            key, label = _readable_key(conn, old, page, root)
            new = paths.entity_path(user_id, key, home_root=home_root)
            old_key = f'{old.parent.name}/{old.stem}'
            page.meta['aliases'] = list(dict.fromkeys([old_key, old.stem, *page.meta.get('aliases', [])]))
            # Replace only generated hash headings, never a user's meaningful title.
            if old.stem[3:].casefold() in page.preamble.casefold() or not page.preamble:
                entries = page.entries
                page.body = re.sub(r'(?m)^# .*$', lambda _: '# ' + label, page.preamble, count=1) or '# ' + label
                write._body(page, entries)
            if new.is_file():
                page = _merge_pages(doc.parse(new.read_text(encoding='utf-8')), page)
            write.atomic_write(new, doc.serialize(page))
            saved = doc.parse(new.read_text(encoding='utf-8'))
            if saved.meta != page.meta or saved.entries != page.entries:
                raise RuntimeError(f'vault rename verification failed: {old}')
            mappings[old_key] = key
            pending.append((old, raw))
            renamed += 1
        # Include previously renamed pages so a crash between copying and
        # rewriting references is safe to resume.
        pages = []
        retiring = {path for path, _ in pending}
        for path in sorted([*vault.glob('entities/*/*.md'), *vault.glob('timeline/*/*/*.md')]):
            if path in retiring:
                continue
            path = paths._guard(vault, path)
            page = doc.parse(path.read_text(encoding='utf-8'))
            pages.append((path, page))
            if path.parts[-3] == 'entities' and not re.fullmatch(r'id-[a-f0-9]{64}', path.stem):
                key = f'{path.parent.name}/{path.stem}'
                for alias in page.meta.get('aliases', []):
                    if re.fullmatch(r'[a-z]+/id-[a-f0-9]{64}', alias):
                        mappings[alias] = key
        names = dict(mappings)
        for old_key, new_key in mappings.items():
            typ, slug = old_key.split('/')
            for name in [slug, f'entities/{typ}/{slug}.md', f'{user_id}/entities/{typ}/{slug}.md']:
                names.setdefault(name, new_key)
        def rewrite(match):
            dst, sep, fragment = match.group(1).strip().partition('#')
            target = names.get(dst)
            return f'[[{target}{sep}{fragment}]]' if target else match.group()
        for path, page in pages:
            raw = path.read_text(encoding='utf-8')
            page.body = re.sub(r'\[\[([^\[\]\n]+)\]\]', rewrite, page.body)
            if path.parts[-3] == 'entities':
                write._body(page, list(dict.fromkeys(page.entries)))
            content = doc.serialize(page)
            if content != raw:
                write.atomic_write(path, content)
        for old, raw in pending:
            # Keep a durable backup; only retire a source after all page writes.
            backup = paths._guard(root, root / 'state/vault-slug-backup' / user_id / old.relative_to(vault))
            if backup.exists() and backup.read_text(encoding='utf-8') != raw:
                raise RuntimeError(f'conflicting vault backup: {backup}')
            write.atomic_write(backup, raw)
            old.unlink()
        index.rebuild(conn, user_id, home_root=home_root)
        names = links.candidates(conn, user_id)
        linked = 0
        for path in sorted(vault.glob('entities/*/*.md')):
            key = f'{path.parent.name}/{path.stem}'
            path = paths.entity_path(user_id, key, home_root=home_root)
            raw = path.read_text(encoding='utf-8')
            page = doc.parse(raw)
            links.update(page, key, names)
            content = doc.serialize(page)
            if content != raw:
                write.atomic_write(path, content)
                linked += 1
        index.rebuild(conn, user_id, home_root=home_root)
        after = resolved()
        moved_paths = {f'{user_id}/entities/{old}.md': f'{user_id}/entities/{new}.md'
                       for old, new in mappings.items()}
        expected = {(moved_paths.get(src, src), moved_paths.get(dst, dst)) for src, dst in before_edges}
        actual = {tuple(r) for r in conn.execute('''SELECT src,dst_resolved FROM vault_links
            WHERE dst_resolved IS NOT NULL AND src IN (SELECT path FROM vault_docs WHERE user_id=?)''', (user_id,))}
        # Full-key and bare-alias links may collapse to one canonical link. Check
        # manual connectivity, not a raw count. Automatic edges are recomputed
        # from live facts and can legitimately disappear after an external edit.
        if expected - actual:
            raise RuntimeError(f'vault repair lost resolved edges for {user_id}: {sorted(expected - actual)}')
        unresolved = [dict(r) for r in conn.execute('''SELECT src,dst FROM vault_links WHERE dst_resolved IS NULL
            AND src IN (SELECT path FROM vault_docs WHERE user_id=?) ORDER BY src,dst''', (user_id,))]
        return {'user_id': user_id, 'renamed': renamed, 'linked_pages': linked,
                'resolved_before': before, 'resolved': after, 'unresolved': unresolved}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Repair vault slugs and links with writers stopped.')
    parser.add_argument('--user', action='append', help='Account to repair (repeatable; default: all vaults)')
    parser.add_argument('--home', type=Path, help='TOMO_HOME override')
    parser.add_argument('--db', type=Path, help='Index DB override')
    args = parser.parse_args()
    root = home._root(args.home)
    database = args.db or root / 'state/tomo.db'
    conn = sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        users = args.user or [p.name for p in sorted((root / 'memory/vault').glob('*')) if p.is_dir()]
        for uid in users:
            print(json.dumps(migrate_vault(conn, uid, home_root=root), ensure_ascii=False))
    finally:
        conn.close()
