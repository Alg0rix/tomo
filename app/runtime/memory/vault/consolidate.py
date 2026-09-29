"""Nightly conversion of prior day notes into durable entity facts."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from . import doc, index, paths, write

log = logging.getLogger(__name__)


def _facts(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError('expected JSON array')
    result = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError('invalid fact')
        paths.entity_key(str(item.get('entity') or ''))
        fact = item.get('fact')
        if not isinstance(fact, str) or not fact.strip():
            raise ValueError('invalid fact')
        result.append({'entity': item['entity'], 'fact': fact.strip(), 'supersedes': str(item.get('supersedes') or '').strip()})
    return result


async def consolidate_day(user_id: str, day: str, client, *, home_root: Path | None = None, conn=None) -> int:
    path = paths.timeline_path(user_id, day, home_root=home_root)
    if not path.is_file() or day >= datetime.now().astimezone().date().isoformat():
        return 0
    with write._lock(user_id):
        page = doc.parse(path.read_text(encoding='utf-8'))
        if page.meta.get('consolidated') == 'true':
            return 0
        source_body = page.body
    response = await asyncio.wait_for(client.complete([
        {'role': 'system', 'content': 'Extract durable facts from this day log. Return ONLY a JSON array of {"entity":"type/slug","fact":"concise declarative fact","supersedes":"optional existing fact text"}. Entity types: person, project, tool, place, org, topic. Return [] if nothing durable.'},
        {'role': 'user', 'content': page.body[:18000]},
    ]), timeout=180)
    facts = _facts(response.content or '[]')
    with write._lock(user_id):
        page = doc.parse(path.read_text(encoding='utf-8'))
        if page.meta.get('consolidated') == 'true':
            return 0
        if page.body != source_body:
            raise RuntimeError('timeline changed during consolidation; retry next run')
        for item in facts:
            if item['supersedes']:
                entity_file = paths.entity_path(user_id, item['entity'], home_root=home_root)
                if entity_file.is_file():
                    old = doc.parse(entity_file.read_text(encoding='utf-8')).entries
                    matches = [i for i, entry in enumerate(old) if item['supersedes'].casefold() in entry.casefold() and not entry.startswith('~~')]
                    if len(matches) == 1:
                        write.forget_fact(user_id, item['entity'], matches[0], home_root=home_root, conn=conn)
            write.add_entity(user_id, item['entity'], item['fact'], source=f'{day}#consolidated', home_root=home_root, conn=conn)
        page.meta['consolidated'] = 'true'
        write.atomic_write(path, doc.serialize(page))
        if conn is None:
            from app.services import store
            store.with_db(lambda db: index.reindex_file(db, user_id, path, home_root=home_root))
        else:
            index.reindex_file(conn, user_id, path, home_root=home_root)
        return len(facts)


async def consolidate_user(user_id: str, client, *, home_root: Path | None = None, conn=None) -> dict:
    root = paths.vault_root(user_id, home_root=home_root)
    result = {'days': 0, 'facts': 0, 'failed': 0}
    for path in sorted(root.glob('timeline/*/*/*.md')):
        try:
            if doc.parse(path.read_text(encoding='utf-8')).meta.get('consolidated') == 'true':
                continue
            count = await consolidate_day(user_id, path.stem, client, home_root=home_root, conn=conn)
            if doc.parse(path.read_text(encoding='utf-8')).meta.get('consolidated') == 'true':
                result['days'] += 1
                result['facts'] += count
        except Exception:
            log.exception('vault consolidation failed for %s %s', user_id, path.stem)
            result['failed'] += 1
    return result


async def run_nightly() -> None:
    from app.runtime.llm import get_llm
    from app.runtime.memory.episodes import optimize_ltm
    from app.services import store

    settings = store.get_settings()
    if not settings.get('memory_consolidation_enabled', False):
        return
    for user in store.list_users():
        uid = user['id']
        try:
            await consolidate_user(uid, get_llm())
            optimize_ltm(user_id=uid)
        except Exception:
            log.exception('nightly memory optimization failed for %s', uid)
