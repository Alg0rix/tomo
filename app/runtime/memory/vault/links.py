"""Conservative mention links, stored before facts so their text stays exact."""
from __future__ import annotations

import re
import sqlite3

from . import doc

_BLOCK = re.compile(r'(?ms)^<!-- auto-links -->\n.*?^<!-- /auto-links -->\n?')
_GENERIC = frozenset({'agent', 'project', 'person', 'place', 'topic', 'tool', 'user', 'org',
                      'profile', 'notes', 'memory', 'procedure', 'experience', 'item'})


def candidates(conn: sqlite3.Connection, user_id: str, *, page: doc.Document | None = None,
               key: str = '') -> dict[str, str]:
    """Aliases include indexed slugs/titles; ambiguous names never imply an edge."""
    names: dict[str, set[str]] = {}
    for row in conn.execute('''SELECT a.alias,d.type,d.slug FROM vault_aliases a
            JOIN vault_docs d ON d.path=a.path WHERE d.user_id=? AND d.kind='entity' ORDER BY a.alias,d.path''', (user_id,)):
        target = f'{row["type"]}/{row["slug"]}'
        if target != key:
            names.setdefault(row['alias'].casefold().strip(), set()).add(target)
    if page is not None:
        slug = key.split('/')[1]
        title = next((line[2:].strip() for line in page.preamble.splitlines() if line.startswith('# ')), slug)
        aliases = page.meta.get('aliases', [])
        for alias in [slug, title, *(aliases if isinstance(aliases, list) else [])]:
            names.setdefault(alias.casefold().strip(), set()).add(key)
    return {alias: next(iter(keys)) for alias, keys in names.items()
            if len(alias) >= 4 and alias not in _GENERIC and len(keys) == 1}


def linkify(text: str, names: dict[str, str], key: str, *, limit: int = 5) -> list[str]:
    # Explicit links remain authoritative; don't interpret their targets as prose.
    text = re.sub(r'\[\[[^\[\]\n]+\]\]', lambda m: ' ' * len(m.group()), text).casefold()
    matches = []
    for alias, target in names.items():
        if target == key:
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
    targets = sorted({target for text in texts for target in linkify(text, names, key)})
    if targets:
        preamble += '\n<!-- auto-links -->\nRelated: ' + ' '.join(f'[[{t}]]' for t in targets) + '\n<!-- /auto-links -->'
    page.body = preamble + ('\n' + '\n'.join('§ ' + e for e in entries) if entries else '')
