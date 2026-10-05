import pytest

from app.core import config
from app.services import store
from tests.fakes.access import admin_client, ensure_stoppers


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Real Member login with an owned RESTRICTED session: document upload
    # runs isolated conversion through the chat container, never the host.
    import os

    monkeypatch.setattr(config, 'TOMO_HOME', tmp_path)
    store.rebind(tmp_path / 'upload.sqlite')
    ensure_stoppers()
    http, admin = admin_client(role='member')
    model = store.create_llm_profile({'name': 'Upload model', 'model': 'test-model', 'api_key': 'k'})
    store.access.assign('usr_admin', admin['id'], 'model', model['id'])
    sid = store.get_or_create_session(store.get_coordinator()['id'], admin['id'])
    # Test-only disk quota: ordinary temporary filesystems charge their entire
    # hard capacity (see test_multi_user_isolation.py). Never copy to prod.
    fs = os.statvfs(store.get_workplace(store.get_session(sid)['workplace_id'])['root_path'])
    capacity_mb = (fs.f_blocks * fs.f_frsize // (1024 * 1024)) + 4096
    store.access.set_quota('usr_admin', admin['id'], {'disk_mb': capacity_mb, 'memory_mb': 2048})
    yield http, admin['id'], sid
    http.close()


def test_upload_large_multiline_reference_to_vault(client):
    http, uid, sid = client
    text = 'Wazzapi documentation.\n§ Literal marker\n' + 'x' * 6000
    result = http.post('/api/memory/upload', data={'entity': 'project/wazzapi', 'session_id': sid},
        files={'file': ('notes.txt', text.encode(), 'text/plain')})
    assert result.status_code == 200
    assert result.json()['added'] is True
    page = http.get('/api/memory/entity/project/wazzapi').json()
    from app.runtime.memory.vault.doc import fact_data
    assert fact_data(page['facts'][0])['text'] == text
    # The isolated seam tags provenance ('uploaded'); the legacy host parser's
    # file-type tag is not reproduced.
    assert page['meta']['tags'] == ['uploaded']
    assert http.get('/memory').status_code == 200


@pytest.mark.parametrize('entity,filename,body', [
    ('tool/../secret', 'notes.txt', b'fact'),
    ('topic/ref', 'notes.txt', b''),
    ('topic/ref', 'image.unsupported', b'not supported'),
])
def test_invalid_uploads_do_not_create_pages(client, entity, filename, body):
    http, uid, sid = client
    result = http.post('/api/memory/upload', data={'entity': entity, 'session_id': sid}, files={'file': (filename, body)})
    assert result.status_code == 400
    assert http.get('/api/memory/graph').json()['nodes'] == []


def test_parser_error_returns_controlled_response(client, monkeypatch):
    # The legacy host doc_parse seam no longer exists: conversion runs
    # isolated anydoc in the chat container. A corrupt office file must still
    # get a controlled 400 (not a 500/503 and no page created).
    http, uid, sid = client
    result = http.post('/api/memory/upload', data={'entity': 'topic/ref', 'session_id': sid}, files={'file': ('broken.pdf', b'%PDF-1.4 garbage not a real document')})
    assert result.status_code == 400
    assert http.get('/api/memory/graph').json()['nodes'] == []


def test_oversized_upload_rejected(client):
    http, uid, sid = client
    result = http.post('/api/memory/upload', data={'entity': 'topic/ref', 'session_id': sid}, files={'file': ('notes.txt', b'x' * (20 * 1024 * 1024 + 1))})
    assert result.status_code == 400


def test_graph_exposes_auto_links_hash_pages_backlinks_and_optional_timeline(client):
    http, uid, sid = client
    from app.runtime.memory.vault import doc, paths, write

    write.add_entity(uid, 'tool/python', 'An implementation language.', aliases=['Python runtime'])
    write.add_entity('other', 'topic/private', 'Private knowledge.', aliases=['Foreign runtime'])
    text = 'Uses Python runtime and [[Python runtime]], not Foreign runtime.'
    result = http.post('/api/memory/upload', data={'entity': 'project/tomo', 'session_id': sid},
        files={'file': ('runtime.txt', text.encode(), 'text/plain')})
    assert result.status_code == 200
    hash_key = 'topic/id-' + 'a' * 64
    write.add_entity(uid, hash_key, 'Python is useful here.')
    day = '2026-09-15'
    path = paths.timeline_path(uid, day)
    write.atomic_write(path, doc.serialize(doc.Document({'date': day}, '## A turn')))
    write.add_entity(uid, 'project/tomo', 'Recorded deployment.', source=paths.timeline_source(uid, day, 'consolidated'))
    graph = http.get('/api/memory/graph').json()
    python_path = f'{uid}/entities/tool/python.md'
    assert len(graph['edges']) == 2  # Aliases to one page count as one edge.
    python = next(n for n in graph['nodes'] if n['id'] == python_path)
    assert python['backlinks'] == 2
    assert any(n['slug'] == hash_key.split('/')[1] for n in graph['nodes'])
    assert all(e['src'].startswith(f'{uid}/') and e['dst_resolved'].startswith(f'{uid}/') for e in graph['edges'])
    full = http.get('/api/memory/graph', params={'include_timeline': 'true'}).json()
    assert any(n['kind'] == 'timeline' for n in full['nodes'])
    assert len(full['edges']) == 3
    overview = http.get('/api/memory/overview').json()
    assert len(overview['links']) == 2
    assert next(e for e in overview['entities'] if e['key'] == 'tool/python')['backlinks'] == 2
    facts = http.get('/api/memory/entity/project/tomo').json()['facts']
    assert doc.fact_data(facts[0])['text'] == text
    human_index = http.get('/api/memory/index')
    assert human_index.status_code == 200
    assert '[[tool/python]]' in human_index.text and 'topic/private' not in human_index.text
    # A truly empty derived index must reproduce the same API graph.
    store.with_db(lambda conn: conn.executescript('DELETE FROM vault_docs; DELETE FROM vault_aliases; DELETE FROM vault_links; DELETE FROM vault_fts;'))
    assert http.get('/api/memory/graph').json() == graph


def test_fact_endpoint_uses_authenticated_owner_and_old_api_removed(client):
    http, uid, sid = client
    result = http.post('/api/memory/facts', json={'entity': 'user/profile', 'content': 'Prefers Indonesian.', 'user_id': 'other'})
    assert result.status_code == 200
    from app.runtime.memory.vault.notes import facts
    assert facts(uid, 'user/profile') == ['Prefers Indonesian.']
    assert facts('other', 'user/profile') == []
    # Removed legacy endpoints fail closed (member default-deny), never 200.
    for endpoint in ['/api/knowledge', '/api/knowledge/upload', '/api/knowledge/kb1']:
        assert http.get(endpoint).status_code in (403, 404)


def test_relations_duplicates_and_merge_api(client):
    http, uid, sid = client
    for entity, content in [('tool/omaxim', 'A host.'), ('tool/tomo', 'Runs on omaxim.'),
                            ('tool/money-plugin', 'Tracks spending.'), ('tool/plugin-money', 'Balance is sample data.')]:
        assert http.post('/api/memory/facts', json={'entity': entity, 'content': content}).status_code == 200
    overview = http.get('/api/memory/overview').json()
    auto = next(e for e in overview['links'] if (e['from'], e['to']) == ('tool/tomo', 'tool/omaxim'))
    assert auto['kind'] == 'auto' and auto['via'] == 'omaxim' and auto['fact'] == 0
    assert overview['relations']['runs_on'] == 'hosts'
    result = http.post('/api/memory/entity/tool/tomo/relations', json={'rel': 'runs_on', 'to': 'tool/omaxim'})
    assert result.json() == {'ok': True, 'changed': True}
    assert http.post('/api/memory/entity/tool/tomo/relations', json={'rel': 'nope', 'to': 'tool/omaxim'}).status_code == 400
    edge = next(e for e in http.get('/api/memory/overview').json()['links'] if e['from'] == 'tool/tomo')
    assert edge['kind'] == 'relation' and edge['rels'] == ['runs_on']
    groups = http.get('/api/memory/duplicates').json()['groups']
    assert [g['keys'] for g in groups] == [['tool/money-plugin', 'tool/plugin-money']]
    merged = http.post('/api/memory/entity/tool/plugin-money/merge', json={'into': 'tool/money-plugin'})
    assert merged.status_code == 200 and merged.json()['facts_added'] == 1
    assert http.get('/api/memory/entity/tool/plugin-money').status_code == 404
    assert http.get('/api/memory/duplicates').json()['groups'] == []
    assert http.post('/api/memory/entity/tool/plugin-money/merge', json={'into': 'tool/money-plugin'}).status_code == 400
