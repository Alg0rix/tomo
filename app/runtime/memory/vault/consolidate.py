"""Nightly conversion of prior day notes into durable entity facts."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from . import doc, index, paths, write
from .relations import VOCAB

log = logging.getLogger(__name__)


def _valid_key(key: str) -> bool:
    try:
        paths.entity_key(key)
    except ValueError:
        return False
    return True


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
        aliases = item.get('aliases', [])
        if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
            raise ValueError('invalid aliases')
        # Relations are optional extras; a malformed one is dropped, not the fact.
        rels = item.get('relations', [])
        rels = [(r['rel'], r['to'].strip()) for r in rels if isinstance(r, dict) and r.get('rel') in VOCAB
                and isinstance(r.get('to'), str) and _valid_key(r['to'].strip())] if isinstance(rels, list) else []
        result.append({'entity': item['entity'], 'fact': fact.strip(), 'supersedes': str(item.get('supersedes') or '').strip(),
                       'aliases': aliases, 'relations': rels})
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
    from .extract import entity_context

    snapshot = entity_context(user_id, home_root=home_root)
    from app.runtime.policy import authorize_model_client
    authorize_model_client(client)
    response = await asyncio.wait_for(client.complete([
        {'role': 'system', 'content': 'Extract durable facts from this day log. Return ONLY a JSON array of {"entity":"type/slug","fact":"concise declarative fact","supersedes":"exact existing fact text or empty","aliases":["names","abbreviations","search terms"]}. Entity types: person, project, tool, place, org, topic. Current facts may be newer than this historical log: never replace newer knowledge with past values. Return [] if nothing durable.'},
        {'role': 'user', 'content': json.dumps({'day': day, 'log': page.body[:18000], 'existing': snapshot}, ensure_ascii=False)[:30000]},
    ]), timeout=180)
    facts = _facts(response.content or '[]')
    with write._lock(user_id):
        page = doc.parse(path.read_text(encoding='utf-8'))
        if page.meta.get('consolidated') == 'true':
            return 0
        if page.body != source_body:
            raise RuntimeError('timeline changed during consolidation; retry next run')
        current = entity_context(user_id, home_root=home_root)
        for item in facts:
            if current.get(item['entity'], []) != snapshot.get(item['entity'], []):
                continue
            write.add_entity(user_id, item['entity'], item['fact'], source=paths.timeline_source(user_id, day, 'consolidated', home_root=home_root),
                             origin='consolidation', aliases=item['aliases'], supersedes=item['supersedes'],
                             home_root=home_root, conn=conn)
        page.meta['consolidated'] = 'true'
        from app.runtime.storage import private_write
        body = doc.serialize(page)
        with private_write(len(body.encode()), home_root=home_root):
            write.atomic_write(path, body)
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
    from app.runtime.llm import get_auxiliary_llm
    from app.runtime.memory.episodes import optimize_ltm
    from app.services import store

    settings = store.get_settings()
    if not settings.get('memory_consolidation_enabled', False):
        return
    for user in store.list_users():
        uid = user['id']
        try:
            from app.runtime.access import execution_scope, AccessDenied
            from app.runtime.supervision import admitted_turn
            context = None
            # No scheduler/global identity or unassigned default model. Choose
            # an existing owned chat with executable CURRENT grants, or skip.
            for session in store.list_sessions(user_id=uid):
                try:
                    context = store.access.resolve_context(uid, session['id'])
                    break
                except AccessDenied:
                    continue
            if context is None:
                continue
            with execution_scope(context):
                async with admitted_turn(context):
                    client = get_auxiliary_llm('memory_consolidation', session_id=context.session_id)
                    try:
                        await consolidate_user(uid, client)
                    finally:
                        if hasattr(client, 'aclose'):
                            await client.aclose()
                    store.access.revalidate(context)
                    optimize_ltm(user_id=uid)
        except Exception:
            log.exception('nightly memory optimization failed for %s', uid)
