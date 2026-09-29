"""Atomic, serialized writes to the Markdown vault."""
from __future__ import annotations

import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from app.runtime.memory import curated
from app.services import store
from . import doc, index, paths

_locks: dict[str, threading.RLock] = {}
_guard = threading.Lock()


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


def add_entity(user_id: str, key: str, fact: str, *, source: str = '', home_root: Path | None = None, conn=None) -> dict:
    typ, slug = paths.entity_key(key)
    fact = (fact or '').strip()
    if not fact or '\n§' in fact:
        raise ValueError('fact is empty or contains an entry delimiter')
    path = paths.entity_path(user_id, key, home_root=home_root)
    with _lock(user_id):
        page = doc.parse(path.read_text(encoding='utf-8')) if path.exists() else doc.Document(
            {'type': typ, 'aliases': [slug], 'tags': [], 'updated': ''}, f'# {slug.replace("-", " ").title()}')
        if curated.near_duplicate(page.entries, fact):
            return {'added': False, 'path': str(path)}
        stamp = datetime.now().astimezone().date().isoformat()
        page.meta['updated'] = stamp
        src = f' (src: [[{source}]])' if source else ''
        page.body = page.body.rstrip() + f'\n§ {fact}{src}'
        atomic_write(path, doc.serialize(page))
        if conn is None:
            store.with_db(lambda db: index.reindex_file(db, user_id, path, home_root=home_root))
        else:
            index.reindex_file(conn, user_id, path, home_root=home_root)
        return {'added': True, 'path': str(path)}


def forget_fact(user_id: str, key: str, number: int, *, home_root: Path | None = None, conn=None) -> bool:
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
    """Use the already available turn text; never make a model call."""
    if not session_id or not (user_message or final_content):
        return
    try:
        if not store.get_settings().get('memory_vault_enabled', False):
            return
        session = store.get_session(session_id)
        if not session:
            return
        summary = f"Goal: {(user_message or '').strip()[:240]}\nOutcome: {final_content.strip()[:480]}"
        append_timeline(session.get('user_id') or 'web', session_id, agent_id or 'main', summary)
    except Exception:
        import logging

        logging.getLogger(__name__).exception('vault turn recording failed')
