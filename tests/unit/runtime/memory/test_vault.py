from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from app.runtime.llm.base import LLMResponse
from app.runtime.memory.vault import doc, index, paths, read, write
from app.runtime.memory.vault.consolidate import consolidate_day


@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_rebuild_links_aliases_and_deletion(tmp_path, db):
    uid = 'alice'
    write.add_entity(uid, 'project/tomo', 'Tomo uses [[tool/python]].', home_root=tmp_path, conn=db)
    write.add_entity(uid, 'tool/python', 'Python is a language.', home_root=tmp_path, conn=db)
    path = paths.entity_path(uid, 'project/tomo', home_root=tmp_path)
    page = doc.parse(path.read_text())
    page.meta['aliases'] = ['friendbot']
    write.atomic_write(path, doc.serialize(page))
    index.rebuild(db, uid, home_root=tmp_path)
    assert db.execute('SELECT count(*) FROM vault_links WHERE dst_resolved IS NOT NULL').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM vault_aliases WHERE alias="friendbot"').fetchone()[0] == 1
    assert 'friendbot' in (paths.vault_root(uid, home_root=tmp_path) / 'index.md').read_text()
    before = [tuple(r) for r in db.execute('SELECT path,kind,type,slug,title,hash FROM vault_docs ORDER BY path')]
    db.executescript('DELETE FROM vault_docs; DELETE FROM vault_aliases; DELETE FROM vault_links; DELETE FROM vault_fts;')
    index.rebuild(db, uid, home_root=tmp_path)
    assert [tuple(r) for r in db.execute('SELECT path,kind,type,slug,title,hash FROM vault_docs ORDER BY path')] == before
    path.unlink()
    index.rebuild(db, uid, home_root=tmp_path)
    assert db.execute('SELECT count(*) FROM vault_docs').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM vault_links').fetchone()[0] == 0


def test_auto_links_survive_rebuild_without_changing_facts(tmp_path, db):
    opts = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/python', 'An implementation language.', aliases=['Python runtime'], **opts)
    write.add_entity('bob', 'tool/private', 'Other account.', aliases=['Private runtime'], **opts)
    text = 'Uses Python runtime, not Private runtime.'
    write.add_entity('alice', 'project/tomo', text, **opts)
    page = doc.parse(paths.entity_path('alice', 'project/tomo', home_root=tmp_path).read_text())
    assert doc.fact_data(page.entries[0])['text'] == text
    assert doc.links(page.body) == ['tool/python']
    source = 'alice/entities/project/tomo.md'
    target = 'alice/entities/tool/python.md'
    assert [r['path'] for r in read.related(db, 'alice', [target])] == [source]
    assert not write.add_entity('alice', 'project/tomo', text, **opts)['added']
    db.executescript('DELETE FROM vault_docs; DELETE FROM vault_aliases; DELETE FROM vault_links; DELETE FROM vault_fts;')
    index.rebuild(db, 'alice', home_root=tmp_path)
    assert [tuple(r) for r in db.execute('SELECT src,dst,dst_resolved FROM vault_links')] == [(source, 'tool/python', target)]
    replacement = 'Uses a different language.'
    assert write.add_entity('alice', 'project/tomo', replacement, supersedes=text, origin='user', **opts)['added']
    assert doc.fact_data(doc.parse(paths.entity_path('alice', 'project/tomo', home_root=tmp_path).read_text()).entries[1])['text'] == replacement
    assert not read.related(db, 'alice', [target])
    write.add_entity('alice', 'project/tomo', 'Python runtime remains useful.', origin='user', **opts)
    assert write.forget_fact('alice', 'project/tomo', 2, **opts)
    assert not read.related(db, 'alice', [target])


def test_auto_links_skip_short_generic_ambiguous_self_and_substrings(tmp_path, db):
    opts = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'person/max', 'A driver.', aliases=['Max', 'F1', 'notes', 'racing'], **opts)
    write.add_entity('alice', 'person/other', 'Another driver.', aliases=['racing'], **opts)
    write.add_entity('alice', 'project/tomo', 'Max uses F1 notes on racing and Tomo. Maximum effort.', **opts)
    assert not db.execute('SELECT * FROM vault_links').fetchall()
    write.add_entity('alice', 'tool/python', 'Python powers Python.', **opts)
    assert not db.execute('SELECT * FROM vault_links').fetchall()


def test_auto_links_choose_longest_alias_and_cap_each_fact(tmp_path, db):
    opts = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/alpha', 'A language.', **opts)
    write.add_entity('alice', 'tool/runtime', 'A runtime.', aliases=['Alpha runtime'], **opts)
    for n in range(6):
        write.add_entity('alice', f'tool/system{n}', 'An implementation.', **opts)
    write.add_entity('alice', 'project/tomo', 'Alpha runtime uses system0 system1 system2 system3 system4 system5.', **opts)
    dsts = {r['dst'] for r in db.execute('SELECT dst FROM vault_links WHERE src="alice/entities/project/tomo.md"')}
    assert 'tool/runtime' in dsts and 'tool/alpha' not in dsts
    assert len(dsts) == 5


def test_timeline_fragments_resolve_only_within_account(tmp_path, db):
    opts = {'home_root': tmp_path, 'conn': db}
    day = '2026-09-15'
    path = paths.timeline_path('alice', day, home_root=tmp_path)
    write.atomic_write(path, doc.serialize(doc.Document({'date': day}, '## A turn')))
    write.add_entity('alice', 'project/tomo', 'A durable fact.', source=day + '#consolidated', **opts)
    write.add_entity('alice', 'tool/python', 'Another fact.', source='alice/timeline/2026/09/2026-09-15.md#turn-ses_1', **opts)
    write.add_entity('bob', 'project/tomo', 'Private fact.', source='alice/timeline/2026/09/2026-09-15.md#consolidated', **opts)
    index.rebuild(db, 'alice', home_root=tmp_path)
    index.rebuild(db, 'bob', home_root=tmp_path)
    links = db.execute('SELECT src,dst_resolved FROM vault_links ORDER BY src').fetchall()
    assert [r['dst_resolved'] for r in links] == ['alice/timeline/2026/09/2026-09-15.md'] * 2 + [None]


def test_writer_dedup_and_scoping(tmp_path, db):
    assert write.add_entity('alice', 'project/tomo', 'A stable project detail.', home_root=tmp_path, conn=db)['added']
    assert not write.add_entity('alice', 'project/tomo', 'A stable project detail.', home_root=tmp_path, conn=db)['added']
    assert db.execute('SELECT count(*) FROM vault_docs WHERE user_id="alice"').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM vault_docs WHERE user_id="bob"').fetchone()[0] == 0
    with pytest.raises(ValueError):
        paths.entity_path('alice', 'project/../secret', home_root=tmp_path)


@pytest.mark.parametrize('goal', ['question\nwith details', None])
def test_record_turn_preserves_goal_with_multiline_outcome(tmp_path, monkeypatch, goal):
    from app.core import config
    from app.services import store

    monkeypatch.setattr(config, 'TOMO_HOME', tmp_path)
    store.rebind(tmp_path / 'timeline.db')
    store.update_settings({'memory_vault_enabled': True})
    from tests.fakes.access import owned_host_session
    sid = owned_host_session()
    uid = store.get_session(sid)['user_id']
    monkeypatch.setattr('app.runtime.memory.vault.extract.schedule_extraction', lambda *args: None)
    write.record_turn(sid, 'main', goal, 'answer\n\n- one\n- two\n- three\n- four\n- five')
    raw = paths.timeline_path(uid, date.today().isoformat()).read_text()
    bullets = [line for line in raw.splitlines() if line.startswith('- ')]
    expected = ['- Goal: question with details'] if goal else []
    assert bullets == [*expected, '- Outcome: answer - one - two - three - four - five']


def test_alias_retrieval_expands_neighbor_with_budget(tmp_path, db):
    uid = 'alice'
    write.add_entity(uid, 'project/tomo', 'Tomo uses [[tool/python]] for its runtime.', home_root=tmp_path, conn=db)
    write.add_entity(uid, 'tool/python', 'Python is the implementation language.', home_root=tmp_path, conn=db)
    path = paths.entity_path(uid, 'project/tomo', home_root=tmp_path)
    page = doc.parse(path.read_text())
    page.meta['aliases'] = ['friendbot']
    write.atomic_write(path, doc.serialize(page))
    output = read.snippet(db, uid, 'friendbot', budget=180, home_root=tmp_path)
    assert '[[project/tomo]]' in output
    assert '[[tool/python]]' in output
    assert len(output) <= 180
    assert not read.snippet(db, 'bob', 'friendbot', home_root=tmp_path)


class Extractor:
    def __init__(self, content='[]', error=False):
        self.content, self.error, self.calls = content, error, 0

    async def complete(self, messages, tools=None):
        self.calls += 1
        if self.error:
            raise RuntimeError('failed')
        return LLMResponse(self.content)


@pytest.mark.asyncio
async def test_consolidation_is_resumable(tmp_path, db):
    day = (date.today() - timedelta(days=1)).isoformat()
    path = paths.timeline_path('alice', day, home_root=tmp_path)
    write.atomic_write(path, doc.serialize(doc.Document({'date': day, 'consolidated': 'false'}, '## 10:00 · session s1 · agent main\n- Tomo is a Python agent.')))
    client = Extractor('[{"entity":"project/tomo","fact":"Tomo is a Python agent."}]')
    assert await consolidate_day('alice', day, client, home_root=tmp_path, conn=db) == 1
    assert await consolidate_day('alice', day, client, home_root=tmp_path, conn=db) == 0
    assert client.calls == 1
    assert doc.parse(path.read_text()).meta['consolidated'] == 'true'
    other = (date.today() - timedelta(days=2)).isoformat()
    failed_path = paths.timeline_path('alice', other, home_root=tmp_path)
    write.atomic_write(failed_path, doc.serialize(doc.Document({'date': other, 'consolidated': 'false'}, '- Another note.')))
    with pytest.raises(RuntimeError):
        await consolidate_day('alice', other, Extractor(error=True), home_root=tmp_path, conn=db)
    assert doc.parse(failed_path.read_text()).meta['consolidated'] == 'false'


def test_edit_move_provenance_and_stale_edits(tmp_path, db):
    options = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/server', 'Server listens on port 8000.', **options)
    assert write.correct_fact('alice', 'tool/server', 0, text='Server listens on port 9000.',
                              expected='Server listens on port 8000.', **options)
    page = doc.parse(paths.entity_path('alice', 'tool/server', home_root=tmp_path).read_text())
    assert page.entries[0].startswith('~~')
    assert doc.fact_data(page.entries[1])['origin'] == 'user'
    assert not write.correct_fact('alice', 'tool/server', 0, text='Stale update', **options)
    assert write.correct_fact('alice', 'tool/server', 1, destination='project/tomo', **options)
    assert '9000' in read.snippet(db, 'alice', 'tomo', **{'home_root': tmp_path})
    assert '8000' not in read.world_card(db, 'alice', home_root=tmp_path)
    assert not read.world_card(db, 'bob', home_root=tmp_path)


@pytest.mark.asyncio
async def test_turn_extraction_supersedes_immediately_with_aliases(tmp_path, db):
    from app.runtime.memory.vault.extract import extract_turn

    write.add_entity('alice', 'tool/server', 'Server listens on port 8000.', home_root=tmp_path, conn=db)
    client = Extractor('[{"entity":"tool/server","fact":"Server listens on port 9000.","supersedes":"Server listens on port 8000.","aliases":["production"]},{"entity":"person/max-verstappen","fact":"The user’s favorite F1 driver.","aliases":["Max","Verstappen","F1","Formula 1"]}]')
    assert await extract_turn('alice', 's1', 'Server moved to 9000. My driver is Max.', '', client,
                              home_root=tmp_path, conn=db) == 2
    page = doc.parse(paths.entity_path('alice', 'tool/server', home_root=tmp_path).read_text())
    assert page.entries[0].startswith('~~')
    assert doc.fact_data(page.entries[1])['origin'] == 'extraction'
    assert '8000' not in read.snippet(db, 'alice', 'production', home_root=tmp_path)
    assert '9000' in read.snippet(db, 'alice', 'production', home_root=tmp_path)
    assert read.search(db, 'alice', 'Formula 1', home_root=tmp_path)[0]['slug'] == 'max-verstappen'
    card = read.world_card(db, 'alice', home_root=tmp_path)
    assert 'favorite F1 driver' in card and '9000' in card and '8000' not in card
    assert await extract_turn('alice', 's2', 'Thanks', '', Extractor(), home_root=tmp_path, conn=db) == 0


@pytest.mark.asyncio
async def test_manual_edit_wins_over_inflight_extraction(tmp_path, db):
    from app.runtime.memory.vault.extract import extract_turn

    write.add_entity('alice', 'tool/server', 'Server listens on port 8000.', home_root=tmp_path, conn=db)

    class EditingExtractor:
        async def complete(self, messages):
            write.correct_fact('alice', 'tool/server', 0, text='Server listens on port 7000.', home_root=tmp_path, conn=db)
            return LLMResponse('[{"entity":"tool/server","fact":"Server listens on port 9000.","supersedes":"Server listens on port 8000."}]')

    assert await extract_turn('alice', 's1', 'Port 9000', '', EditingExtractor(), home_root=tmp_path, conn=db) == 0
    assert '7000' in read.world_card(db, 'alice', home_root=tmp_path)
    assert '9000' not in read.world_card(db, 'alice', home_root=tmp_path)


@pytest.mark.asyncio
async def test_extraction_limits_and_forgotten_fact_stays_forgotten(tmp_path, db):
    from app.runtime.memory.vault.extract import extract_turn

    options = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'person/max', 'Favorite driver.', **options)
    write.forget_fact('alice', 'person/max', 0, **options)
    client = Extractor('[{"entity":"person/max","fact":"Favorite driver."}]')
    assert await extract_turn('alice', 's1', 'Favorite driver', '', client, **options) == 0
    assert not read.world_card(db, 'alice', home_root=tmp_path)
    import json
    client = Extractor(json.dumps([{'entity': 'topic/test', 'fact': 'Fact'}] * 4))
    with pytest.raises(ValueError, match='three'):
        await extract_turn('alice', 's2', 'test', '', client, **options)
    assert not paths.entity_path('alice', 'topic/test', home_root=tmp_path).exists()


@pytest.mark.asyncio
async def test_background_extraction_is_serial_and_next_turn_waits(tmp_path, monkeypatch):
    import asyncio
    from app.runtime.memory.vault import extract
    from app.services import store
    from tests.fakes.access import owned_host_session

    store.rebind(tmp_path / 'extract-serial.db')
    sid = owned_host_session()
    uid = store.get_session(sid)['user_id']
    calls = []
    gate = asyncio.Event()

    async def fake_extract(user_id, session_id, message, final, client):
        if message == 'first':
            await gate.wait()
        calls.append(message)

    monkeypatch.setattr(extract, 'extract_turn', fake_extract)
    monkeypatch.setattr(extract, 'extraction_client', lambda session_id=None: object())
    from app.runtime.access import execution_scope
    with execution_scope(store.access.resolve_context(uid, sid)):
        extract.schedule_extraction(uid, sid, 'first', '')
        extract.schedule_extraction(uid, sid, 'second', '')
    waiter = asyncio.create_task(extract.wait_for_extraction(uid))
    await asyncio.sleep(0)
    assert not waiter.done()
    gate.set()
    await waiter
    assert calls == ['first', 'second']
    assert uid not in extract._pending


def test_move_does_not_drop_fact_that_only_looks_similar(tmp_path, db):
    options = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/server', 'Server listens on port 9000.', **options)
    write.add_entity('alice', 'project/tomo', 'Server listens on port 8000.', **options)
    assert write.correct_fact('alice', 'tool/server', 0, destination='project/tomo', **options)
    page = doc.parse(paths.entity_path('alice', 'project/tomo', home_root=tmp_path).read_text())
    assert any('9000' in e and not e.startswith('~~') for e in page.entries)


def test_existing_new_fact_can_still_retire_old_fact(tmp_path, db):
    options = {'home_root': tmp_path, 'conn': db}
    write.add_entity('alice', 'tool/server', 'Server listens on port 8000.', **options)
    write.add_entity('alice', 'tool/server', 'Current server port is 9000.', **options)
    result = write.add_entity('alice', 'tool/server', 'Current server port is 9000.',
                              supersedes='Server listens on port 8000.', origin='extraction', **options)
    assert result['superseded']
    assert '8000' not in read.world_card(db, 'alice', home_root=tmp_path)
