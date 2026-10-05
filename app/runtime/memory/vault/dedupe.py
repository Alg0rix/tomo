"""Find pages that describe the same thing and fold one into another.

Merging is explicit (UI, agent tool, or operator); detection only suggests.
The source page is backed up outside the vault before it is removed.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from app.core import home
from . import doc, index, links, paths, relations, write

SELF = frozenset({'me', 'user', 'self', 'the-user', 'myself', 'saya', 'aku'})


def _norm(slug: str) -> str:
    slug = re.sub(r'-[0-9a-f]{8}$', '', slug.casefold())
    tokens = [t for t in re.split(r'[-_ ]+', slug) if t]
    # money-plugin == plugin-money == money; tomo-plugins (a collection) stays distinct.
    if len(tokens) > 1 and tokens[-1] == 'plugin':
        tokens.pop()
    if len(tokens) > 1 and tokens[0] == 'plugin':
        tokens.pop(0)
    return ''.join(t[:-1] if len(t) > 3 and t.endswith('s') else t for t in tokens)


def is_self(user_id: str, key: str, aliases: list[str]) -> bool:
    """A person page that is really the account owner."""
    typ, slug = key.split('/', 1)
    own = {user_id.casefold(), user_id.casefold().replace('_', '-')}
    return typ == 'person' and (slug in SELF | own or slug.startswith('usr-')
                                or bool({a.casefold() for a in aliases} & (SELF | own)))


def duplicates(conn, user_id: str, *, home_root: Path | None = None) -> list[dict]:
    index.rebuild(conn, user_id, home_root=home_root)
    rows = conn.execute("SELECT path,type,slug,title,body FROM vault_docs WHERE user_id=? AND kind='entity' ORDER BY type,slug",
                        (user_id,)).fetchall()
    keys = [f'{r["type"]}/{r["slug"]}' for r in rows]
    title = {k: (r['title'] or r['slug']) for k, r in zip(keys, rows)}
    live = {k: sum(not e.startswith('~~') for e in doc.parse(r['body']).entries) for k, r in zip(keys, rows)}
    aliases: dict[str, set[str]] = {k: set() for k in keys}
    by_path = dict(zip((r['path'] for r in rows), keys))
    for r in conn.execute('SELECT alias,path FROM vault_aliases WHERE path IN (SELECT path FROM vault_docs WHERE user_id=?)', (user_id,)):
        if r['path'] in by_path:
            aliases[by_path[r['path']]].add(r['alias'].casefold())
    parent = {k: k for k in keys}
    reasons: dict[str, list[str]] = {}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def join(a, b, why):
        parent[find(a)] = find(b)
        reasons.setdefault(a, []).append(why)

    # Same name after normalising slug or title. Aliases are not compared: a part
    # (tool/omaxim-k3s) legitimately carries its parent's name as a search alias.
    seen: dict[str, str] = {}
    for key in keys:
        typ, slug = key.split('/', 1)
        own = {_norm(slug)} | (set() if typ == 'agent' or links._noise(title[key].casefold()) else {_norm(title[key])})
        for norm in sorted(n for n in own if len(n) >= 3):
            if norm in seen and find(seen[norm]) != find(key):
                join(key, seen[norm], f'same name as {seen[norm]}')
            seen.setdefault(norm, key)
    if 'user/profile' in parent:
        for key in keys:
            if find(key) != find('user/profile') and is_self(user_id, key, sorted(aliases[key])):
                join(key, 'user/profile', 'describes you')
    groups: dict[str, list[str]] = {}
    for key in keys:
        groups.setdefault(find(key), []).append(key)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda k: (k != 'user/profile', -live[k], k))
        out.append({'keys': members, 'into': members[0],
                    'reasons': [r for k in members for r in reasons.get(k, [])]})
    return sorted(out, key=lambda g: g['into'])


def _title(page: doc.Document, slug: str) -> str:
    return next((line[2:].strip() for line in page.preamble.splitlines() if line.startswith('# ')), slug)


def merge_entity(user_id: str, source: str, target: str, *, home_root: Path | None = None, conn=None) -> dict:
    """Fold source into target: facts, aliases, tags and relations; old links keep resolving."""
    if conn is None:
        from app.services import store
        return store.with_db(lambda db: merge_entity(user_id, source, target, home_root=home_root, conn=db))
    src_type, src_slug = paths.entity_key(source)
    dst_type, dst_slug = paths.entity_key(target)
    if source == target:
        raise ValueError('cannot merge a page into itself')
    src_path = paths.entity_path(user_id, source, home_root=home_root)
    dst_path = paths.entity_path(user_id, target, home_root=home_root)
    vault = paths.vault_root(user_id, home_root=home_root)
    with write._lock(user_id):
        if not src_path.is_file():
            raise ValueError('page not found')
        src_raw = src_path.read_text(encoding='utf-8')
        src = doc.parse(src_raw)
        src_title = _title(src, src_slug)
        if dst_path.is_file():
            dst_raw = dst_path.read_text(encoding='utf-8')
            dst = doc.parse(dst_raw)
        else:
            dst_raw = ''
            dst = doc.Document({'type': dst_type, 'aliases': [], 'tags': [], 'updated': ''}, f'# {src_title}')
        stamp = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')
        backup = home._root(home_root) / 'state' / 'vault-merge-backup' / user_id / f'{stamp}-{src_type}-{src_slug}.md'
        write.atomic_write(backup, src_raw)
        if dst_raw:
            write.atomic_write(backup.with_name(f'{stamp}-{dst_type}-{dst_slug}.into.md'), dst_raw)

        entries = dst.entries
        known = {doc.fact_data(e)['text'].casefold() for e in entries}
        added = 0
        for entry in src.entries:
            text = doc.fact_data(entry)['text'].casefold()
            if text not in known:
                entries.append(entry)
                known.add(text)
                added += not entry.startswith('~~')
        def listed(page, field):
            value = page.meta.get(field, [])
            return value if isinstance(value, list) else []
        names = [*listed(dst, 'aliases'), source, src_title, src_slug, *listed(src, 'aliases')]
        dst.meta['aliases'] = write._aliases(dst_slug, [n for n in names if n.casefold() not in SELF])
        dst.meta['tags'] = list(dict.fromkeys([*listed(dst, 'tags'), *listed(src, 'tags')]))
        moved = lambda k: target if k.casefold() == source.casefold() else k
        rels = [(r, moved(k)) for r, k in [*relations.parse(dst), *relations.parse(src)]]
        rels = [(r, k) for r, k in dict.fromkeys(rels) if k.casefold() != target.casefold()]
        strip = lambda page: links._BLOCK.sub('', relations.BLOCK.sub('', page.preamble))
        extras = [line for line in strip(src).splitlines() if line.strip() and not line.startswith('# ')]
        head = strip(dst).rstrip().splitlines() or [f'# {src_title}']
        head += [line for line in extras if line not in head]
        dst.body = '\n'.join(head)
        write._body(dst, entries)
        relations.store(dst, rels)
        dst.meta['updated'] = datetime.now().astimezone().date().isoformat()
        write.atomic_write(dst_path, doc.serialize(dst))

        # Point every explicit link at the surviving page, then drop the source.
        spellings = {s.casefold() for s in (source, f'entities/{source}.md', f'{user_id}/entities/{source}.md')}
        def rewrite(match):
            dst_, sep, frag = match.group(1).strip().partition('#')
            return f'[[{target}{sep}{frag}]]' if dst_.casefold() in spellings else match.group()
        for path in sorted([*vault.glob('entities/*/*.md'), *vault.glob('timeline/*/*/*.md')]):
            if path == src_path:
                continue
            raw = path.read_text(encoding='utf-8')
            content = re.sub(r'\[\[([^\[\]\n]+)\]\]', rewrite, raw)
            if content != raw:
                write.atomic_write(path, content)
        src_path.unlink()
        index.rebuild(conn, user_id, home_root=home_root)
        relinked = relink(conn, user_id, home_root=home_root)
        return {'merged': source, 'into': target, 'facts_added': added, 'relinked': relinked, 'backup': str(backup)}


def relink(conn, user_id: str, *, home_root: Path | None = None) -> int:
    """Recompute every page's automatic links against the current names."""
    vault = paths.vault_root(user_id, home_root=home_root)
    names = links.candidates(conn, user_id)
    changed = 0
    for path in sorted(vault.glob('entities/*/*.md')):
        key = f'{path.parent.name}/{path.stem}'
        path = paths.entity_path(user_id, key, home_root=home_root)
        raw = path.read_text(encoding='utf-8')
        page = doc.parse(raw)
        links.update(page, key, names)
        content = doc.serialize(page)
        if content != raw:
            write.atomic_write(path, content)
            changed += 1
    if changed:
        index.rebuild(conn, user_id, home_root=home_root)
    return changed
