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
    write.add_entity(uid, 'tool/python', 'Python powers Tomo.', home_root=tmp_path, conn=db)
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


def test_writer_dedup_and_scoping(tmp_path, db):
    assert write.add_entity('alice', 'project/tomo', 'A stable project detail.', home_root=tmp_path, conn=db)['added']
    assert not write.add_entity('alice', 'project/tomo', 'A stable project detail.', home_root=tmp_path, conn=db)['added']
    assert db.execute('SELECT count(*) FROM vault_docs WHERE user_id="alice"').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM vault_docs WHERE user_id="bob"').fetchone()[0] == 0
    with pytest.raises(ValueError):
        paths.entity_path('alice', 'project/../secret', home_root=tmp_path)


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
