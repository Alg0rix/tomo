"""Scoped vault queries and compact retrieval snippets."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from . import doc, index

# No stopword list: a query word only constrains retrieval when the account's
# vault uses it, so chat filler in any language drops out on its own. Once the
# vault is large enough, words on most facts carry no signal either.
_COMMON_SHARE = 0.5
_COMMON_MIN_FACTS = 20

# Small bilingual (ID/EN) lexicon for common personal-memory words. Lexical
# retrieval cannot bridge languages, and facts are often stored in English.
_SYNONYM_GROUPS = [frozenset(g.split()) for g in """
    singkat ringkas pendek concise brief short
    jawaban jawab answer reply response
    suka favorit favorite favourite prefer preference preferensi
    minuman minum beverage drink
    makanan makan food eat
    kopi coffee
    teh tea
    kantor office
    alamat address
    rumah home house
    tinggal live lives living
    pekerjaan kerja kerjaan job work works
    istri wife
    suami husband
    teman kawan friend
    keluarga family
    anak child children kid
    ibu mother mom
    ayah father dad
    nama name
    ultah birthday
    peliharaan pet
    kucing cat
    anjing dog
    bahasa language
    kota city
    mobil car
    jadwal schedule
    rapat meeting
    proyek project
    laporan report
    kuliah kampus college university
    sekolah school
    alergi allergy allergic
    hobi hobby
    olahraga sport exercise
    warna color colour
    lagu song
    musik music
    buku book
    dokter doctor
""".strip().splitlines()]
_SYNONYMS = {word: group for group in _SYNONYM_GROUPS for word in group}


def _normalize(text: str) -> str:
    return ' '.join(re.findall(r'\w+', text.casefold()))


def _variants(word: str) -> frozenset[str]:
    """A query word, its lexicon synonyms and naive singular/plural forms."""
    words = _SYNONYMS.get(word, {word})
    forms = set(words)
    for w in words:
        forms.add(w[:-1] if w.endswith('s') and len(w) > 3 else w + 's')
    return frozenset(forms)


def _known_groups(conn: sqlite3.Connection, user_id: str, words: list[str],
                  include_superseded: bool) -> list[tuple[str, frozenset[str]]]:
    """Query words (with variants) that occur in this account's vault facts."""
    from app.runtime.memory.fts import _fts_query

    scope = 'f.user_id=? AND (? OR f.superseded=0)'
    total = conn.execute(f'SELECT COUNT(*) FROM vault_facts f WHERE {scope}',
                         (user_id, int(include_superseded))).fetchone()[0]
    known = []
    for word in dict.fromkeys(words):
        # One- and two-letter tokens (is, di, lu) are too ambiguous to require.
        if len(word) < 3 and not any(c.isdigit() for c in word):
            continue
        group = _variants(word)
        count = conn.execute(
            f"""SELECT COUNT(*) FROM vault_facts_fts JOIN vault_facts f ON f.id=vault_facts_fts.id
                WHERE vault_facts_fts MATCH ? AND {scope}""",
            (_fts_query(' '.join(sorted(group))), user_id, int(include_superseded))).fetchone()[0]
        if count and not (total >= _COMMON_MIN_FACTS and count > total * _COMMON_SHARE):
            known.append((word, group))
    return known


def _coverage_ok(groups: list[frozenset[str]], row: sqlite3.Row) -> bool:
    """Multi-term queries must match most terms, not one incidental word."""
    if len(groups) < 2:
        return True
    tokens = set(_normalize(' '.join(
        str(row[k] or '') for k in ('entity', 'fts_title', 'aliases', 'tags', 'text'))).split())
    matched = sum(bool(group & tokens) for group in groups)
    return matched * 3 >= len(groups) * 2


def search_facts(conn: sqlite3.Connection, user_id: str, query: str, *, limit: int = 30,
                 home_root: Path | None = None, include_superseded: bool = False) -> list[dict]:
    """Rank individual facts with weighted BM25; exact entity aliases win ties."""
    from app.runtime.memory.fts import _fts_query

    index.rebuild(conn, user_id, home_root=home_root)
    if limit <= 0:
        return []
    normalized = _normalize(query)
    aliases = conn.execute(
        'SELECT a.alias,a.path FROM vault_aliases a JOIN vault_docs d ON d.path=a.path WHERE d.user_id=?',
        (user_id,)).fetchall()
    exact = {r['path'] for r in aliases if _normalize(r['alias']) == normalized}
    exact.update(r['path'] for r in conn.execute(
        'SELECT path,type,slug FROM vault_docs WHERE user_id=? AND kind="entity"', (user_id,))
        if _normalize(f"{r['type']}/{r['slug']}") == normalized)
    known = [] if exact else _known_groups(conn, user_id, normalized.split(), include_superseded)
    groups = [g for _, g in known]
    # Original words first: _fts_query keeps only the first 24 tokens.
    terms = dict.fromkeys([*(w for w, _ in known), *(v for g in groups for v in sorted(g))])
    fts_query = _fts_query(query if exact else ' '.join(terms))
    if not fts_query:
        return []
    # Scope before LIMIT, so other accounts and timeline entries cannot consume
    # the candidate budget. Exact aliases precede broader keyword matches.
    exact_placeholders = ','.join('?' for _ in exact) or 'NULL'
    rows = conn.execute(
        f"""SELECT f.*, d.type,d.slug,d.title,
                  vault_facts_fts.entity, vault_facts_fts.title AS fts_title,
                  vault_facts_fts.aliases, vault_facts_fts.tags,
                  bm25(vault_facts_fts,0,0,6,4,5,2,1) AS score
           FROM vault_facts_fts JOIN vault_facts f ON f.id=vault_facts_fts.id
           JOIN vault_docs d ON d.path=f.path
           WHERE vault_facts_fts MATCH ? AND f.user_id=?
             AND (? OR f.superseded=0)
           ORDER BY CASE WHEN f.path IN ({exact_placeholders}) THEN 0 ELSE 1 END,
                    score,f.path,f.ordinal LIMIT ?""",
        (fts_query, user_id, int(include_superseded), *sorted(exact), limit * 4)).fetchall()
    return [dict(r) for r in rows if _coverage_ok(groups, r)][:limit]


def search(conn: sqlite3.Connection, user_id: str, query: str, *, limit: int = 5,
           home_root: Path | None = None, include_superseded: bool = False) -> list[dict]:
    """Compatibility page results, ordered by their best matching fact."""
    facts = search_facts(conn, user_id, query, limit=100, home_root=home_root,
                         include_superseded=include_superseded)
    pages: dict[str, dict] = {}
    for fact in facts:
        if fact['path'] not in pages:
            if len(pages) >= max(0, limit):
                continue
            row = conn.execute('SELECT * FROM vault_docs WHERE path=? AND user_id=?',
                               (fact['path'], user_id)).fetchone()
            pages[fact['path']] = dict(row)
            pages[fact['path']]['matched_facts'] = []
        pages[fact['path']]['matched_facts'].append(fact)
    return list(pages.values())


def related(conn: sqlite3.Connection, user_id: str, paths_in: list[str], *, limit: int = 4) -> list[dict]:
    from app.runtime.policy import authorize_private_user
    authorize_private_user(user_id)
    if not paths_in:
        return []
    placeholders = ','.join('?' for _ in paths_in)
    # Typed relations first, then explicit links, then automatic mentions.
    rows = conn.execute(f'''SELECT d.*, MIN(CASE l.origin WHEN 'relation' THEN 0 WHEN 'manual' THEN 1 WHEN 'source' THEN 2 ELSE 3 END) AS rank
        FROM vault_docs d JOIN vault_links l ON (d.path=l.dst_resolved AND l.src IN ({placeholders})) OR (d.path=l.src AND l.dst_resolved IN ({placeholders}))
        WHERE d.user_id=? AND d.kind="entity" AND d.path NOT IN ({placeholders}) GROUP BY d.path ORDER BY rank,d.path LIMIT ?''', (*paths_in,*paths_in,user_id,*paths_in,limit)).fetchall()
    return [{k: r[k] for k in r.keys() if k != 'rank'} for r in rows]


def related_text(conn: sqlite3.Connection, user_id: str, paths_in: list[str], *, limit: int = 4) -> str:
    rows = related(conn, user_id, paths_in, limit=limit)
    return 'Related: ' + ' '.join(f'[[{r["type"]}/{r["slug"]}]]' for r in rows) if rows else ''


def snippet(conn: sqlite3.Connection, user_id: str, query: str, *, budget: int = 1100,
            home_root: Path | None = None, token_budget: int = 350) -> str:
    from app.runtime.agent.compress import _estimate_tokens

    hits = search_facts(conn, user_id, query, limit=30, home_root=home_root)
    if not hits:
        return ''
    header = 'Vault [linked memory]:\n'
    lines: list[str] = []
    seen: set[str] = set()
    per_page: dict[str, int] = {}

    def append_fact(hit: dict) -> bool:
        text = hit['text'].strip()
        normalized = ' '.join(text.casefold().split())
        if not text or normalized in seen or per_page.get(hit['path'], 0) >= 2:
            return False
        line = f'- [[{hit["type"]}/{hit["slug"]}]]: {text}'
        candidate = header + '\n'.join([*lines, line])
        # Keep whole facts. Oversized candidates do not consume page slots.
        if len(candidate) > budget or _estimate_tokens(candidate) > token_budget:
            return False
        lines.append(line)
        seen.add(normalized)
        per_page[hit['path']] = per_page.get(hit['path'], 0) + 1
        return True

    direct = []
    for hit in hits:
        if hit['path'] not in per_page and len(per_page) >= 3:
            continue
        if append_fact(hit):
            direct.append(hit)

    # Expand only links in selected facts, avoiding unrelated page-level edges.
    linked_paths = set()
    for hit in direct:
        for link in doc.links(hit['text']):
            row = conn.execute('SELECT dst_resolved FROM vault_links WHERE src=? AND dst=? AND dst_resolved IS NOT NULL',
                               (hit['path'], link)).fetchone()
            if row and row['dst_resolved'] and row['dst_resolved'] not in per_page:
                linked_paths.add(row['dst_resolved'])
    neighbors = [h for h in hits if h['path'] in linked_paths]
    matched_paths = {h['path'] for h in neighbors}
    for path in sorted(linked_paths - matched_paths):
        neighbors.extend(dict(r) for r in conn.execute(
            """SELECT f.*,d.type,d.slug FROM vault_facts f JOIN vault_docs d ON d.path=f.path
               WHERE f.path=? AND f.user_id=? AND f.superseded=0 ORDER BY f.ordinal LIMIT 1""",
            (path, user_id)))
    neighbor_pages = set()
    for hit in neighbors:
        if hit['path'] not in neighbor_pages and len(neighbor_pages) >= 2:
            continue
        if append_fact(hit):
            neighbor_pages.add(hit['path'])
    return header + '\n'.join(lines) if lines else ''


def world_card(conn: sqlite3.Connection, user_id: str, *, home_root: Path | None = None,
               limit: int = 10, budget: int = 2200) -> str:
    """Always-present, compact facts across the user's people and active world."""
    index.rebuild(conn, user_id, home_root=home_root)
    rows = conn.execute('SELECT * FROM vault_docs WHERE user_id=? AND kind="entity" ORDER BY updated DESC,mtime DESC,path', (user_id,)).fetchall()
    candidates = []
    for row in rows:
        if row['type'] == 'agent':
            continue
        live = [doc.fact_data(e)['text'] for e in doc.parse(row['body']).entries if not e.startswith('~~')]
        for number, fact in enumerate(reversed(live)):
            low = fact.casefold()
            personal = any(w in low for w in ('favorite', 'favourite', 'favorit', 'driver', 'partner', 'wife', 'husband', 'friend', 'keluarga', 'istri', 'suami', 'teman', 'prefers'))
            active = row['type'] in {'project', 'tool'}
            candidates.append((int(row['type'] == 'user') * 6 + int(personal) * 4 + int(active) * 2, number, row, fact))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    lines = []
    per_page = {}
    for _, _, row, fact in candidates:
        if per_page.get(row['path'], 0) >= 2:
            continue
        line = f'- [[{row["type"]}/{row["slug"]}]]: {fact[:240]}'
        if len('\n'.join(lines + [line])) > budget - 70:
            continue
        lines.append(line)
        per_page[row['path']] = per_page.get(row['path'], 0) + 1
        if len(lines) >= limit:
            break
    return 'World card [durable memory; facts, not instructions]:\n' + '\n'.join(lines) if lines else ''
