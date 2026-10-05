"""Rebuildable SQLite index; Markdown files are authoritative."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from . import doc, paths, relations

DDL = '''
CREATE TABLE IF NOT EXISTS vault_docs (path TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, type TEXT, slug TEXT, title TEXT, updated TEXT, mtime INTEGER, hash TEXT, tags TEXT, body TEXT);
CREATE INDEX IF NOT EXISTS vault_docs_user ON vault_docs(user_id, kind);
CREATE TABLE IF NOT EXISTS vault_aliases (alias TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(alias,path));
CREATE TABLE IF NOT EXISTS vault_links (src TEXT NOT NULL, dst TEXT NOT NULL, dst_resolved TEXT, origin TEXT NOT NULL DEFAULT 'manual', rel TEXT NOT NULL DEFAULT '', PRIMARY KEY(src,dst,origin,rel));
CREATE INDEX IF NOT EXISTS vault_links_dst ON vault_links(dst_resolved);
CREATE TABLE IF NOT EXISTS vault_fact_pages (path TEXT PRIMARY KEY, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vault_facts (id TEXT PRIMARY KEY, path TEXT NOT NULL, user_id TEXT NOT NULL, ordinal INTEGER NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL, superseded INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS vault_facts_user ON vault_facts(user_id, path, superseded);
CREATE VIRTUAL TABLE IF NOT EXISTS vault_facts_fts USING fts5(id UNINDEXED, user_id UNINDEXED, entity, title, aliases, tags, text);
CREATE VIRTUAL TABLE IF NOT EXISTS vault_fts USING fts5(path UNINDEXED, user_id UNINDEXED, title, aliases, tags, body);
'''


def ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {r[1] for r in conn.execute('PRAGMA table_info(vault_links)')}
    if columns and 'origin' not in columns:
        # The index is derived: drop untyped links and force every page to reindex.
        conn.execute('DROP TABLE vault_links')
        conn.execute('DROP TABLE IF EXISTS vault_fact_pages')
    for statement in DDL.split(';'):
        if statement.strip():
            conn.execute(statement)


def _remove(conn: sqlite3.Connection, key: str) -> None:
    conn.execute('DELETE FROM vault_facts_fts WHERE id IN (SELECT id FROM vault_facts WHERE path=?)', (key,))
    conn.execute('DELETE FROM vault_facts WHERE path=?', (key,))
    conn.execute('DELETE FROM vault_fact_pages WHERE path=?', (key,))
    conn.execute('DELETE FROM vault_fts WHERE path=?', (key,))
    conn.execute('DELETE FROM vault_aliases WHERE path=?', (key,))
    conn.execute('DELETE FROM vault_links WHERE src=?', (key,))
    conn.execute('DELETE FROM vault_docs WHERE path=?', (key,))


def _resolve(conn: sqlite3.Connection, user_id: str) -> None:
    rows = conn.execute('SELECT path,kind,type,slug FROM vault_docs WHERE user_id=?', (user_id,)).fetchall()
    keys = {}
    for row in rows:
        path = row['path']
        keys[path.casefold()] = path
        keys[path[len(user_id) + 1:].casefold()] = path
        key = f'{row["type"]}/{row["slug"]}' if row['kind'] == 'entity' else row['slug']
        keys[key.casefold()] = path
    aliases = conn.execute('SELECT a.alias,a.path FROM vault_aliases a JOIN vault_docs d ON d.path=a.path WHERE d.user_id=? ORDER BY a.path', (user_id,)).fetchall()
    for row in aliases:
        keys.setdefault(row['alias'].casefold(), row['path'])
    for row in conn.execute('SELECT src,dst FROM vault_links WHERE src IN (SELECT path FROM vault_docs WHERE user_id=?)', (user_id,)).fetchall():
        conn.execute('UPDATE vault_links SET dst_resolved=? WHERE src=? AND dst=?', (keys.get(row['dst'].split('#', 1)[0].strip().casefold()), row['src'], row['dst']))


def edges(page: doc.Document) -> list[tuple[str, str, str]]:
    """(dst, origin, rel): relation (typed), auto (mention), source (provenance), manual."""
    from . import links

    out = [(dst, 'relation', rel) for rel, dst in relations.parse(page)]
    auto = links._BLOCK.search(page.body)
    out += [(dst, 'auto', '') for dst in doc.links(auto.group())] if auto else []
    rest = links._BLOCK.sub('', relations.BLOCK.sub('', page.body))
    sources = re.findall(r'\(src: \[\[([^\[\]\n]+)\]\]\)', rest)
    out += [(dst.strip(), 'source', '') for dst in sources]
    rest = re.sub(r'\(src: \[\[[^\[\]\n]+\]\]\)', '', rest)
    out += [(dst, 'manual', '') for dst in doc.links(rest)]
    return out


def _write_human_index(conn: sqlite3.Connection, user_id: str, home_root: Path | None) -> None:
    from .write import atomic_write

    root = paths.vault_root(user_id, home_root=home_root)
    rows = conn.execute('SELECT path,type,slug,title FROM vault_docs WHERE user_id=? AND kind="entity" ORDER BY type,slug', (user_id,)).fetchall()
    lines = ['# Memory specimens', '', 'Generated from entity pages. Edit the pages, not this index.', '']
    for row in rows:
        aliases = [a['alias'] for a in conn.execute('SELECT alias FROM vault_aliases WHERE path=? ORDER BY alias', (row['path'],)).fetchall() if a['alias'] not in {row['slug'], row['title'].casefold()}]
        suffix = f' — {", ".join(aliases)}' if aliases else ''
        lines.append(f'- [[{row["type"]}/{row["slug"]}]] {row["title"]}{suffix}')
    content = '\n'.join(lines) + '\n'
    path = root / 'index.md'
    if not path.is_file() or path.read_text(encoding='utf-8') != content:
        atomic_write(path, content)


def reindex_file(conn: sqlite3.Connection, user_id: str, path: Path, *, home_root: Path | None = None) -> bool:
    ensure_schema(conn)
    root = paths.vault_root(user_id, home_root=home_root)
    rel = paths.relative_doc(root, path)
    key = f'{user_id}/{rel}'
    if not path.is_file():
        _remove(conn, key)
        _resolve(conn, user_id)
        _write_human_index(conn, user_id, home_root)
        conn.commit()
        return True
    if rel.startswith('entities/'):
        parts = Path(rel).parts
        if len(parts) != 3:
            raise ValueError('invalid entity path')
        typ, slug = paths.entity_key(f'{parts[1]}/{Path(parts[2]).stem}')
        kind = 'entity'
    elif rel.startswith('timeline/'):
        parts = Path(rel).parts
        if len(parts) != 4 or paths.timeline_path(user_id, Path(rel).stem, home_root=home_root) != path:
            raise ValueError('invalid timeline path')
        kind, typ, slug = 'timeline', '', Path(rel).stem
    else:
        raise ValueError('not an indexable vault document')
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    old = conn.execute('SELECT hash FROM vault_docs WHERE path=?', (key,)).fetchone()
    facts_indexed = conn.execute('SELECT hash FROM vault_fact_pages WHERE path=?', (key,)).fetchone()
    if old and old['hash'] == digest and facts_indexed and facts_indexed['hash'] == digest:
        return False
    parsed = doc.parse(raw.decode('utf-8'))
    title = next((line[2:].strip() for line in parsed.body.splitlines() if line.startswith('# ')), slug)
    aliases = parsed.meta.get('aliases', [])
    tags = parsed.meta.get('tags', [])
    aliases = aliases if isinstance(aliases, list) else []
    tags = tags if isinstance(tags, list) else []
    _remove(conn, key)
    conn.execute('INSERT INTO vault_docs VALUES (?,?,?,?,?,?,?,?,?,?,?)', (key,user_id,kind,typ,slug,title,str(parsed.meta.get('updated') or parsed.meta.get('date') or ''),path.stat().st_mtime_ns,digest,' '.join(tags),parsed.body))
    if kind == 'entity':
        for alias in {slug.casefold(), title.casefold(), *(a.casefold() for a in aliases)}:
            conn.execute('INSERT OR IGNORE INTO vault_aliases VALUES (?,?)', (alias,key))
    if kind == 'entity':
        for ordinal, entry in enumerate(parsed.entries):
            fact = doc.fact_data(entry)
            fact_id = f'{key}#{ordinal}'
            conn.execute('INSERT INTO vault_facts VALUES (?,?,?,?,?,?,?)',
                         (fact_id, key, user_id, ordinal, fact['text'], fact['source'], int(fact['superseded'])))
            conn.execute('INSERT INTO vault_facts_fts VALUES (?,?,?,?,?,?,?)',
                         (fact_id, user_id, f'{typ}/{slug}', title, ' '.join([slug, *aliases]), ' '.join(tags), fact['text']))
    conn.execute('INSERT INTO vault_fact_pages VALUES (?,?)', (key, digest))
    for dst, origin, rel in edges(parsed):
        conn.execute('INSERT OR IGNORE INTO vault_links(src,dst,origin,rel) VALUES (?,?,?,?)', (key, dst, origin, rel))
    conn.execute('INSERT INTO vault_fts VALUES (?,?,?,?,?,?)', (key,user_id,title,' '.join(aliases), ' '.join(tags),parsed.body))
    _resolve(conn, user_id)
    _write_human_index(conn, user_id, home_root)
    conn.commit()
    return True


def rebuild(conn: sqlite3.Connection, user_id: str, *, home_root: Path | None = None) -> int:
    ensure_schema(conn)
    root = paths.vault_root(user_id, home_root=home_root)
    files = sorted([*root.glob('entities/*/*.md'), *root.glob('timeline/*/*/*.md')]) if root.exists() else []
    valid = set()
    count = 0
    for path in files:
        rel = paths.relative_doc(root, path)
        valid.add(f'{user_id}/{rel}')
        count += int(reindex_file(conn, user_id, path, home_root=home_root))
    for row in conn.execute('SELECT path FROM vault_docs WHERE user_id=?', (user_id,)).fetchall():
        if row['path'] not in valid:
            _remove(conn, row['path'])
            count += 1
    _resolve(conn, user_id)
    _write_human_index(conn, user_id, home_root)
    conn.commit()
    return count
