"""Conservative mention links, stored before facts so their text stays exact."""
from __future__ import annotations

import re
import sqlite3

from . import doc

_BLOCK = re.compile(r'(?ms)^<!-- auto-links -->\n.*?^<!-- /auto-links -->\n?')
_GENERIC = frozenset({'agent', 'project', 'person', 'place', 'topic', 'tool', 'user', 'org',
                      'profile', 'notes', 'memory', 'procedure', 'experience', 'item'})


def _fragments(slug: str, title: str) -> set[str]:
    """Single words of a multi-part slug are not names of the page ("2026", "source")."""
    whole = {slug.casefold(), slug.replace('-', ' ').replace('_', ' ').casefold(), title.casefold()}
    return {part.casefold() for part in re.split('[-_ ]', slug) if part} - whole


def _noise(alias: str) -> bool:
    return len(alias) < 4 or alias in _GENERIC or not re.search(r'[^\W\d_]', alias)


def _common(conn: sqlite3.Connection, user_id: str, words: set[str]) -> set[str]:
    """Single words mentioned by many pages are vocabulary, not names."""
    if not words:
        return set()
    seen: dict[str, int] = {}
    pages = 0
    for row in conn.execute("SELECT body FROM vault_docs WHERE user_id=? AND kind='entity'", (user_id,)):
        pages += 1
        for word in words & set(re.findall(r'\w+', _BLOCK.sub('', row['body']).casefold())):
            seen[word] = seen.get(word, 0) + 1
    limit = max(6, pages * 0.08)
    return {word for word, count in seen.items() if count > limit}


def candidates(conn: sqlite3.Connection, user_id: str, *, page: doc.Document | None = None,
               key: str = '') -> dict[str, str]:
    """Aliases include indexed slugs/titles; ambiguous names never imply an edge."""
    names: dict[str, set[str]] = {}
    dropped: set[str] = set()
    for row in conn.execute('''SELECT a.alias,d.type,d.slug,d.title FROM vault_aliases a
            JOIN vault_docs d ON d.path=a.path WHERE d.user_id=? AND d.kind='entity' ORDER BY a.alias,d.path''', (user_id,)):
        target = f'{row["type"]}/{row["slug"]}'
        alias = row['alias'].casefold().strip()
        if alias in _fragments(row['slug'], row['title'] or ''):
            dropped.add(alias)  # Still claims the word, so no other page owns it by accident.
        if target != key:
            names.setdefault(alias, set()).add(target)
    if page is not None:
        slug = key.split('/')[1]
        title = next((line[2:].strip() for line in page.preamble.splitlines() if line.startswith('# ')), slug)
        aliases = page.meta.get('aliases', [])
        for alias in [slug, title, *(aliases if isinstance(aliases, list) else [])]:
            alias = alias.casefold().strip()
            if alias in _fragments(slug, title):
                dropped.add(alias)
            names.setdefault(alias, set()).add(key)
    single = {alias for alias in names if re.fullmatch(r'\w+', alias)}
    dropped |= _common(conn, user_id, single - dropped)
    return {alias: next(iter(keys)) for alias, keys in names.items()
            if not _noise(alias) and alias not in dropped and len(keys) == 1}


def linkify(text: str, names: dict[str, str], key: str, *, limit: int = 5) -> list[str]:
    # Explicit links remain authoritative; don't interpret their targets as prose.
    text = re.sub(r'\[\[[^\[\]\n]+\]\]', lambda m: ' ' * len(m.group()), text).casefold()
    matches = []
    for alias, target in names.items():
        # Cheap substring test first: compiling one pattern per name is the hot path.
        if target == key or alias not in text:
            continue
        for match in re.finditer(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', text):
            matches.append((match.start(), match.end(), target))
    matches.sort(key=lambda m: (-(m[1] - m[0]), m[0], m[2]))
    occupied, found = [], []
    for start, end, target in matches:
        if any(start < b and end > a for a, b in occupied):
            continue
        occupied.append((start, end))
        if target not in found:
            found.append(target)
            if len(found) == limit:
                break
    return found


def manual_links(page: doc.Document) -> list[str]:
    return doc.links(_BLOCK.sub('', page.preamble) + '\n' + '\n'.join(page.entries))


def update(page: doc.Document, key: str, names: dict[str, str]) -> None:
    """Recompute only automatic links from live facts and the page's aliases."""
    preamble = _BLOCK.sub('', page.preamble).rstrip()
    entries = page.entries
    texts = [doc.fact_data(e)['text'] for e in entries if not e.startswith('~~')]
    aliases = page.meta.get('aliases', [])
    if texts and isinstance(aliases, list):
        texts.append(' '.join(aliases))
    # Explicit links and relations already say more than a mention would.
    explicit = {link.casefold() for link in manual_links(page)}
    targets = sorted({target for text in texts for target in linkify(text, names, key)} - explicit)
    if targets:
        preamble += '\n<!-- auto-links -->\nRelated: ' + ' '.join(f'[[{t}]]' for t in targets) + '\n<!-- /auto-links -->'
    page.body = preamble + ('\n' + '\n'.join('§ ' + e for e in entries) if entries else '')
