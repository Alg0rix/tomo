from __future__ import annotations

import json
import sqlite3

import pytest

from app.runtime.llm.base import LLMResponse
from app.runtime.memory.vault import dedupe, doc, index, links, paths, read, relations, write


@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def page(tmp_path, key, uid='alice'):
    return doc.parse(paths.entity_path(uid, key, home_root=tmp_path).read_text(encoding='utf-8'))


def edges(db, src, uid='alice'):
    return {(r['dst'], r['origin'], r['rel']) for r in db.execute(
        'SELECT dst,origin,rel FROM vault_links WHERE src=?', (f'{uid}/entities/{src}.md',))}


def test_slug_fragments_years_and_common_words_never_link(tmp_path, db):
    opts = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'topic/f1-2026-season', 'The 2026 Formula 1 calendar.', **opts)
    write.add_entity('alice', 'topic/f1-data-source', 'Jolpica API feeds race data.', **opts)
    assert 'data' not in page(tmp_path, 'topic/f1-data-source').meta['aliases']
    # Legacy pages still carry fragment aliases on disk; they must not become names.
    legacy = page(tmp_path, 'topic/f1-data-source')
    legacy.meta['aliases'] = [*legacy.meta['aliases'], 'source', 'data']
    write.atomic_write(paths.entity_path('alice', 'topic/f1-data-source', home_root=tmp_path), doc.serialize(legacy))
    write.add_entity('alice', 'tool/nginx', 'Config changed in 2026; source lives in /etc.', **opts)
    assert edges(db, 'tool/nginx') == set()
    names = links.candidates(db, 'alice')
    assert {'2026', 'source', 'data', 'season'}.isdisjoint(names)
    assert names['f1 2026 season'] == 'topic/f1-2026-season'
    write.add_entity('alice', 'tool/grafana', 'Reads the f1 data source for dashboards.', **opts)
    assert ('topic/f1-data-source', 'auto', '') in edges(db, 'tool/grafana')


def test_word_on_many_pages_is_vocabulary_not_a_name(tmp_path, db):
    opts = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/cache', 'A cache.', aliases=['cache'], **opts)
    for n in range(8):
        write.add_entity('alice', f'topic/t{n}', f'Page {n} clears the cache daily.', **opts)
    assert 'cache' not in links.candidates(db, 'alice')
    # Pages written before the word became common are fixed by the startup relink.
    dedupe.relink(db, 'alice', home_root=tmp_path)
    assert all(('tool/cache', 'auto', '') not in edges(db, f'topic/t{n}') for n in range(8))


def test_relations_block_round_trips_and_is_typed_in_index(tmp_path, db):
    opts = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/omaxim', 'A host.', **opts)
    write.add_entity('alice', 'tool/tomo', 'An agent runtime; see [[topic/notes]]. Runs on omaxim.', **opts)
    assert ('tool/omaxim', 'auto', '') in edges(db, 'tool/tomo')
    assert write.relate('alice', 'tool/tomo', 'runs_on', 'tool/omaxim', **opts)
    assert not write.relate('alice', 'tool/tomo', 'runs_on', 'tool/omaxim', **opts)
    saved = page(tmp_path, 'tool/tomo')
    assert relations.parse(saved) == [('runs_on', 'tool/omaxim')]
    assert saved.body.splitlines()[:4] == ['# Tomo', '<!-- relations -->', '- runs_on: [[tool/omaxim]]', '<!-- /relations -->']
    # The typed edge replaces the weaker automatic one; facts are untouched.
    assert edges(db, 'tool/tomo') == {('tool/omaxim', 'relation', 'runs_on'), ('topic/notes', 'manual', '')}
    assert [doc.fact_data(e)['text'] for e in saved.entries] == ['An agent runtime; see [[topic/notes]]. Runs on omaxim.']
    write.add_entity('alice', 'tool/tomo', 'Second fact.', **opts)
    assert relations.parse(page(tmp_path, 'tool/tomo')) == [('runs_on', 'tool/omaxim')]
    assert read.related(db, 'alice', ['alice/entities/tool/tomo.md'])[0]['slug'] == 'omaxim'
    with pytest.raises(ValueError):
        write.relate('alice', 'tool/tomo', 'loves', 'tool/omaxim', **opts)
    with pytest.raises(ValueError):
        write.relate('alice', 'tool/tomo', 'uses', 'tool/tomo', **opts)
    assert write.relate('alice', 'tool/tomo', 'runs_on', 'tool/omaxim', remove=True, **opts)
    assert relations.parse(page(tmp_path, 'tool/tomo')) == []
    assert ('tool/omaxim', 'auto', '') in edges(db, 'tool/tomo')


def test_old_untyped_link_index_is_rebuilt(tmp_path, db):
    db.executescript('CREATE TABLE vault_links (src TEXT NOT NULL, dst TEXT NOT NULL, dst_resolved TEXT, PRIMARY KEY(src,dst));'
                     'CREATE TABLE vault_fact_pages (path TEXT PRIMARY KEY, hash TEXT NOT NULL);')
    path = paths.entity_path('alice', 'tool/a', home_root=tmp_path)
    write.atomic_write(path, '---\ntype: tool\n---\n# A\n§ Uses [[tool/b]].')
    index.rebuild(db, 'alice', home_root=tmp_path)
    assert edges(db, 'tool/a') == {('tool/b', 'manual', '')}


def test_duplicates_and_merge_keep_facts_links_and_backup(tmp_path, db):
    opts = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/money-plugin', 'Tracks expenses.', **opts)
    write.add_entity('alice', 'tool/plugin-money', 'Balance went negative from sample data.', **opts)
    write.add_entity('alice', 'tool/plugin-money', 'Old fact.', **opts)
    write.forget_fact('alice', 'tool/plugin-money', 1, **opts)
    write.relate('alice', 'tool/plugin-money', 'part_of', 'project/tomo', **opts)
    write.relate('alice', 'tool/plugin-money', 'uses', 'tool/money-plugin', **opts)
    write.add_entity('alice', 'project/tomo', 'Ships [[tool/plugin-money]].', **opts)
    write.add_entity('alice', 'user/profile', 'Prefers short answers.', **opts)
    write.add_entity('alice', 'person/alex', 'Takes vitamin D.', aliases=['user'], **opts)
    groups = dedupe.duplicates(db, 'alice', home_root=tmp_path)
    assert {tuple(g['keys']) for g in groups} == {('tool/money-plugin', 'tool/plugin-money'), ('user/profile', 'person/alex')}
    result = dedupe.merge_entity('alice', 'tool/plugin-money', 'tool/money-plugin', **opts)
    assert result['facts_added'] == 1
    assert not paths.entity_path('alice', 'tool/plugin-money', home_root=tmp_path).exists()
    merged = page(tmp_path, 'tool/money-plugin')
    texts = [doc.fact_data(e) for e in merged.entries]
    assert [(f['text'], f['superseded']) for f in texts] == [
        ('Tracks expenses.', False), ('Balance went negative from sample data.', False), ('Old fact.', True)]
    assert relations.parse(merged) == [('part_of', 'project/tomo')]
    assert 'tool/plugin-money' in merged.meta['aliases']
    assert '[[tool/money-plugin]]' in paths.entity_path('alice', 'project/tomo', home_root=tmp_path).read_text()
    assert ('tool/money-plugin', 'manual', '') in edges(db, 'project/tomo')
    backup = tmp_path / 'state/vault-merge-backup/alice'
    assert any('plugin-money' in p.name for p in backup.iterdir())
    assert read.search(db, 'alice', 'sample data negative', home_root=tmp_path)[0]['slug'] == 'money-plugin'
    with pytest.raises(ValueError):
        dedupe.merge_entity('alice', 'tool/plugin-money', 'tool/money-plugin', **opts)
    dedupe.merge_entity('alice', 'person/alex', 'user/profile', **opts)
    profile = page(tmp_path, 'user/profile')
    assert 'user' not in profile.meta['aliases'] and 'Alex' in profile.meta['aliases']
    assert dedupe.duplicates(db, 'alice', home_root=tmp_path) == []


class Extractor:
    def __init__(self, content):
        self.content = content

    async def complete(self, messages, tools=None):
        return LLMResponse(self.content)


@pytest.mark.asyncio
async def test_extraction_adds_relations_and_folds_self_pages_into_profile(tmp_path, db):
    from app.runtime.memory.vault.extract import extract_turn

    opts = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/omaxim', 'A host.', **opts)
    client = Extractor(json.dumps([
        {'entity': 'tool/tomo', 'fact': 'Tomo service restarts nightly.', 'aliases': [],
         'relations': [{'rel': 'runs_on', 'to': 'tool/omaxim'}, {'rel': 'loves', 'to': 'tool/x'}, {'rel': 'uses', 'to': '../x'}]},
        {'entity': 'person/alex', 'fact': 'Allergic to penicillin.', 'aliases': ['Alex', 'me']},
    ]))
    assert await extract_turn('alice', 's1', 'tomo restarts nightly on omaxim; I am allergic to penicillin', '', client, **opts) == 2
    assert relations.parse(page(tmp_path, 'tool/tomo')) == [('runs_on', 'tool/omaxim')]
    assert not paths.entity_path('alice', 'person/alex', home_root=tmp_path).exists()
    profile = page(tmp_path, 'user/profile')
    assert doc.fact_data(profile.entries[0])['text'] == 'Allergic to penicillin.'
    assert 'me' not in profile.meta['aliases']


def test_memory_tool_relate_and_merge(tmp_path, db, monkeypatch):
    from app.runtime.tools import memory, user_ctx
    from app.services import store

    monkeypatch.setattr(user_ctx, 'current_user_id', lambda: 'alice')
    monkeypatch.setattr('app.runtime.policy.authorize_tool', lambda *a, **k: None)
    monkeypatch.setattr(store, 'with_db', lambda fn: fn(db))
    from app.core import home
    monkeypatch.setattr(home, '_root', lambda root=None: tmp_path)
    write.add_entity('alice', 'tool/a', 'A.', conn=db)
    write.add_entity('alice', 'tool/b', 'B.', conn=db)
    write.add_entity('alice', 'tool/bs', 'B again.', conn=db)
    assert memory.run({'action': 'relate', 'entity': 'tool/a', 'rel': 'uses', 'to': 'tool/b'}).startswith('Related')
    assert memory.run({'action': 'relate', 'entity': 'tool/a', 'rel': 'uses', 'to': 'tool/b'}) == 'Relation already present.'
    assert memory.run({'action': 'relate', 'entity': 'tool/a', 'rel': 'bogus', 'to': 'tool/b'}).startswith('Error')
    assert memory.run({'action': 'merge', 'entity': 'tool/bs', 'into': 'tool/b'}).startswith('Merged')
    assert memory.run({'action': 'unrelate', 'entity': 'tool/a', 'rel': 'uses', 'to': 'tool/b'}) == 'Removed relation.'


def test_name_normalisation_for_duplicates():
    assert dedupe._norm('money-plugin') == dedupe._norm('plugin-money') == dedupe._norm('money')
    assert dedupe._norm('tomo-plugin-sdk') == dedupe._norm('tomo-plugins-sdk')
    assert dedupe._norm('tomo-plugins') != dedupe._norm('tomo')
    assert dedupe._norm('cadesia_ops-281da75b') == dedupe._norm('cadesia_ops')
