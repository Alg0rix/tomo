"""Scoped vault queries and compact retrieval snippets."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from . import doc, index, paths


def search(conn: sqlite3.Connection, user_id: str, query: str, *, limit: int = 5, home_root: Path | None = None) -> list[dict]:
    from app.runtime.memory.fts import _fts_query

    index.rebuild(conn, user_id, home_root=home_root)
    words = [w.casefold() for w in query.split() if len(w) > 1]
    if not words:
        return []
    fts_query = _fts_query(query)
    fts_paths = []
    if fts_query:
        fts_paths = [r['path'] for r in conn.execute(
            'SELECT path FROM vault_fts WHERE vault_fts MATCH ? AND user_id=? ORDER BY rank LIMIT 30',
            (fts_query, user_id)).fetchall()]
    rows = conn.execute('SELECT * FROM vault_docs WHERE user_id=? AND kind="entity"', (user_id,)).fetchall()
    aliases = conn.execute('SELECT alias,path FROM vault_aliases WHERE path IN (SELECT path FROM vault_docs WHERE user_id=?)', (user_id,)).fetchall()
    by_path: dict[str, list[str]] = {}
    for row in aliases:
        by_path.setdefault(row['path'], []).append(row['alias'])
    ranked = []
    for row in rows:
        hay = f'{row["title"]} {row["tags"]} {row["body"]}'.casefold()
        matches = sum(3 if any(w in a for a in by_path.get(row['path'], [])) else 1 if w in hay else 0 for w in words)
        if row['path'] in fts_paths:
            matches += 2 + max(0, 10 - fts_paths.index(row['path'])) / 10
        if matches:
            ranked.append((matches, dict(row)))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]['path']))
    return [row for _, row in ranked[:limit]]


def related(conn: sqlite3.Connection, user_id: str, paths_in: list[str], *, limit: int = 4) -> list[dict]:
    if not paths_in:
        return []
    placeholders = ','.join('?' for _ in paths_in)
    rows = conn.execute(f'''SELECT DISTINCT d.* FROM vault_docs d JOIN vault_links l ON (d.path=l.dst_resolved AND l.src IN ({placeholders})) OR (d.path=l.src AND l.dst_resolved IN ({placeholders})) WHERE d.user_id=? AND d.kind="entity" ORDER BY d.path LIMIT ?''', (*paths_in,*paths_in,user_id,limit)).fetchall()
    return [dict(r) for r in rows if r['path'] not in paths_in][:limit]


def snippet(conn: sqlite3.Connection, user_id: str, query: str, *, budget: int = 1100, home_root: Path | None = None) -> str:
    hits = search(conn, user_id, query, limit=3, home_root=home_root)
    neighbors = related(conn, user_id, [h['path'] for h in hits], limit=2)
    lines = []
    for row in [*hits, *neighbors]:
        facts = [e for e in doc.parse(row['body']).entries if not e.startswith('~~')]
        if facts:
            lines.append(f'- [[{row["type"]}/{row["slug"]}]]: {facts[0][:220]}')
    if hits:
        days = conn.execute('SELECT body FROM vault_docs WHERE user_id=? AND kind="timeline" ORDER BY slug DESC LIMIT 3', (user_id,)).fetchall()
        for day in days:
            for line in day['body'].splitlines():
                if line.startswith('- ') and any(h['slug'] in line.casefold() for h in hits):
                    lines.append(line[:180])
                    break
    return ('Vault [linked memory]:\n' + '\n'.join(lines))[:budget] if lines else ''
