"""Atomic, serialized writes to the Markdown vault."""
from __future__ import annotations

import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from . import doc, index, paths

_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()


def near_duplicate(entries: list[str], content: str) -> str | None:
    needle = " ".join(content.casefold().split())
    for entry in entries:
        normalized = " ".join(entry.casefold().split())
        shorter, longer = sorted((normalized, needle), key=len)
        if normalized == needle or (len(shorter) >= 24 and shorter in longer):
            return entry
    return None


def _lock(user_id: str) -> threading.RLock:
    with _guard:
        return _locks.setdefault(user_id, threading.RLock())


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.vault-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _aliases(slug: str, aliases: list[str] | None) -> list[str]:
    candidates = [slug, slug.replace('-', ' ').replace('_', ' '), *re.split('[-_]', slug), *(aliases or [])]
    clean = [re.sub(r'[\r\n,\[\]]', ' ', a).strip()[:80] for a in candidates if isinstance(a, str)]
    return list(dict.fromkeys(a for a in clean if a))[:20]


def _save(user_id: str, path: Path, page: doc.Document, home_root, conn) -> None:
    from app.services import store

    page.meta['updated'] = datetime.now().astimezone().date().isoformat()
    atomic_write(path, doc.serialize(page))
    if conn is None:
        store.with_db(lambda db: index.reindex_file(db, user_id, path, home_root=home_root))
    else:
        index.reindex_file(conn, user_id, path, home_root=home_root)


def _body(page: doc.Document, entries: list[str]) -> None:
    heading = page.body.split('§', 1)[0].rstrip()
    page.body = heading + '\n' + '\n'.join('§ ' + entry for entry in entries)


def add_entity(user_id: str, key: str, fact: str, *, source: str = '', origin: str = 'agent',
               aliases: list[str] | None = None, supersedes: str = '', tags: list[str] | None = None,
               home_root: Path | None = None, conn=None) -> dict:
    typ, slug = paths.entity_key(key)
    if aliases is not None and (not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases)):
        raise ValueError('invalid aliases')
    if tags is not None and (not isinstance(tags, list) or any(not isinstance(t, str) or any(c in t for c in '\r\n,[]') for t in tags)):
        raise ValueError('invalid tags')
    fact = (fact or '').strip()
    if not fact or fact.startswith('~~'):
        raise ValueError('invalid fact')
    if origin not in {'agent', 'consolidation', 'user', 'extraction'}:
        raise ValueError('invalid fact origin')
    if any(c in source for c in '\n\r[]'):
        raise ValueError('invalid source')
    path = paths.entity_path(user_id, key, home_root=home_root)
    with _lock(user_id):
        page = doc.parse(path.read_text(encoding='utf-8')) if path.exists() else doc.Document(
            {'type': typ, 'aliases': [], 'tags': [], 'updated': ''}, f'# {slug.replace("-", " ").title()}')
        if tags is not None:
            page.meta['tags'] = list(dict.fromkeys([*page.meta.get('tags', []), *tags]))
        entries = page.entries
        matches = [i for i, e in enumerate(entries) if not e.startswith('~~') and
                   supersedes and doc.fact_data(e)['text'] == supersedes]
        if origin in {'extraction', 'consolidation'} and any(
                e.startswith('~~') and doc.fact_data(e)['text'] == fact for e in entries):
            return {'added': False, 'path': str(path)}
        # Supersession is exact and atomic, never fuzzy: port changes often look like duplicates.
        live = [doc.fact_data(e)['text'] for i, e in enumerate(entries) if not e.startswith('~~') and i not in matches]
        old_aliases = page.meta.get('aliases', [])
        page.meta['aliases'] = _aliases(slug, [*(old_aliases if isinstance(old_aliases, list) else []), *(aliases or [])])
        if supersedes and len(matches) != 1:
            return {'added': False, 'path': str(path), 'conflict': True}
        stamp = datetime.now().astimezone().date().isoformat()
        duplicate = (any(e.casefold() == fact.casefold() for e in live) if origin == 'user'
                     else near_duplicate(live, fact))
        for i in matches:
            entries[i] = f'~~{entries[i]}~~ superseded {stamp}'
        if duplicate:
            _body(page, entries)
            _save(user_id, path, page, home_root, conn)
            return {'added': False, 'superseded': bool(matches), 'path': str(path)}
        src = f' (src: [[{source}]])' if source else ''
        encoded = doc.encode_fact(fact)
        entries.append(f'{encoded} (origin: {origin}){src}')
        _body(page, entries)
        _save(user_id, path, page, home_root, conn)
        return {'added': True, 'path': str(path)}


def correct_fact(user_id: str, key: str, number: int, *, text: str | None = None,
                 destination: str | None = None, expected: str | None = None,
                 home_root: Path | None = None, conn=None) -> bool:
    """Keep the old entry as history; UI changes have user provenance."""
    path = paths.entity_path(user_id, key, home_root=home_root)
    if destination:
        paths.entity_key(destination)
    with _lock(user_id):
        if not path.is_file():
            return False
        page = doc.parse(path.read_text(encoding='utf-8'))
        if number < 0 or number >= len(page.entries):
            return False
        entry = page.entries[number]
        data = doc.fact_data(entry)
        if data['superseded'] or (expected is not None and data['text'] != expected):
            return False
        replacement = data['text'] if text is None else text.strip()
        if (destination or key) == key:
            if replacement == data['text']:
                return True
            result = add_entity(user_id, key, replacement, source=data['source'], origin='user',
                                supersedes=data['text'], home_root=home_root, conn=conn)
            return result['added'] or result.get('superseded', False)
        # Validate/write destination before retiring the source.
        add_entity(user_id, destination, replacement, source=data['source'], origin='user',
                   home_root=home_root, conn=conn)
        return forget_fact(user_id, key, number, home_root=home_root, conn=conn)


def forget_fact(user_id: str, key: str, number: int, *, home_root: Path | None = None, conn=None) -> bool:
    from app.services import store

    path = paths.entity_path(user_id, key, home_root=home_root)
    with _lock(user_id):
        if not path.is_file():
            return False
        page = doc.parse(path.read_text(encoding='utf-8'))
        entries = page.entries
        if number < 0 or number >= len(entries) or entries[number].startswith('~~'):
            return False
        entries[number] = f'~~{entries[number]}~~ superseded {datetime.now().astimezone().date().isoformat()}'
        heading = next((line for line in page.body.splitlines() if line.startswith('# ')), f'# {key.split("/")[1]}')
        page.body = heading + '\n' + '\n'.join('§ ' + entry for entry in entries)
        page.meta['updated'] = datetime.now().astimezone().date().isoformat()
        atomic_write(path, doc.serialize(page))
        if conn is None:
            store.with_db(lambda db: index.reindex_file(db, user_id, path, home_root=home_root))
        else:
            index.reindex_file(conn, user_id, path, home_root=home_root)
        return True


def append_timeline(user_id: str, session_id: str, agent_id: str, summary: str, *, home_root: Path | None = None, conn=None) -> Path:
    from app.services import store

    now = datetime.now().astimezone()
    path = paths.timeline_path(user_id, now.date().isoformat(), home_root=home_root)
    safe_session = re.sub(r'[^A-Za-z0-9_.:@-]', '', session_id)[:128]
    safe_agent = re.sub(r'[^A-Za-z0-9_.:@-]', '', agent_id)[:128]
    bullets = [line.strip()[:400] for line in summary.splitlines() if line.strip() and line.strip() != '---'][-4:]
    if not bullets:
        return path
    with _lock(user_id):
        page = doc.parse(path.read_text(encoding='utf-8')) if path.exists() else doc.Document(
            {'date': now.date().isoformat(), 'consolidated': 'false'}, '')
        block = f'## {now:%H:%M} · session {safe_session} · agent {safe_agent}\n' + '\n'.join('- ' + line for line in bullets)
        page.body = (page.body.rstrip() + '\n' + block).strip()
        page.meta['consolidated'] = 'false'
        atomic_write(path, doc.serialize(page))
        if conn is None:
            store.with_db(lambda db: index.reindex_file(db, user_id, path, home_root=home_root))
        else:
            index.reindex_file(conn, user_id, path, home_root=home_root)
    return path


def record_turn(session_id: str | None, agent_id: str | None, user_message: str | None, final_content: str) -> None:
    """Record timeline immediately and schedule automatic background extraction."""
    from app.services import store

    if not session_id or not (user_message or final_content):
        return
    try:
        if not store.get_settings().get('memory_vault_enabled', False):
            return
        session = store.get_session(session_id)
        if not session:
            return
        # Keep each field on one bullet: append_timeline limits physical lines.
        goal = ' '.join((user_message or '').split())[:240]
        outcome = ' '.join(final_content.split())[:480]
        summary = '\n'.join([*([f'Goal: {goal}'] if goal else []), f'Outcome: {outcome}'])
        uid = session.get('user_id') or 'web'
        append_timeline(uid, session_id, agent_id or 'main', summary)
        from .extract import schedule_extraction

        schedule_extraction(uid, session_id, user_message or '', final_content)
    except Exception:
        import logging

        logging.getLogger(__name__).exception('vault turn recording failed')
