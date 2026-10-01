import pytest
from fastapi.testclient import TestClient

from app.core import config
from app.core.deps import require_auth
from app.main import app
from app.services import store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'TOMO_HOME', tmp_path)
    store.rebind(tmp_path / 'upload.sqlite')
    app.dependency_overrides[require_auth] = lambda: None
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(require_auth, None)


def test_upload_large_multiline_reference_to_vault(client):
    text = 'Wazzapi documentation.\n§ Literal marker\n' + 'x' * 6000
    result = client.post('/api/memory/upload', data={'entity': 'project/wazzapi'},
        files={'file': ('notes.txt', text.encode(), 'text/plain')})
    assert result.status_code == 200
    assert result.json()['added'] is True
    page = client.get('/api/memory/entity/project/wazzapi').json()
    from app.runtime.memory.vault.doc import fact_data
    assert fact_data(page['facts'][0])['text'] == text
    assert page['meta']['tags'] == ['uploaded', 'text']
    assert client.get('/memory').status_code == 200


@pytest.mark.parametrize('entity,filename,body', [
    ('tool/../secret', 'notes.txt', b'fact'),
    ('topic/ref', 'notes.txt', b''),
    ('topic/ref', 'image.unsupported', b'not supported'),
])
def test_invalid_uploads_do_not_create_pages(client, entity, filename, body):
    result = client.post('/api/memory/upload', data={'entity': entity}, files={'file': (filename, body)})
    assert result.status_code == 400
    assert client.get('/api/memory/graph').json()['nodes'] == []


def test_parser_error_returns_controlled_response(client, monkeypatch):
    def fail(*args):
        raise RuntimeError('parser failure')
    monkeypatch.setattr('app.services.doc_parse.parse_document', fail)
    result = client.post('/api/memory/upload', data={'entity': 'topic/ref'}, files={'file': ('notes.txt', b'fact')})
    assert result.status_code == 400


def test_oversized_upload_rejected(client):
    result = client.post('/api/memory/upload', data={'entity': 'topic/ref'}, files={'file': ('notes.txt', b'x' * (20 * 1024 * 1024 + 1))})
    assert result.status_code == 400


def test_graph_exposes_auto_links_hash_pages_backlinks_and_optional_timeline(client):
    from app.runtime.memory.vault import doc, paths, write

    write.add_entity('web', 'tool/python', 'An implementation language.', aliases=['Python runtime'])
    write.add_entity('other', 'topic/private', 'Private knowledge.', aliases=['Foreign runtime'])
    text = 'Uses Python runtime and [[Python runtime]], not Foreign runtime.'
    result = client.post('/api/memory/upload', data={'entity': 'project/tomo'},
        files={'file': ('runtime.txt', text.encode(), 'text/plain')})
    assert result.status_code == 200
    hash_key = 'topic/id-' + 'a' * 64
    write.add_entity('web', hash_key, 'Python is useful here.')
    day = '2026-09-15'
    path = paths.timeline_path('web', day)
    write.atomic_write(path, doc.serialize(doc.Document({'date': day}, '## A turn')))
    write.add_entity('web', 'project/tomo', 'Recorded deployment.', source=paths.timeline_source('web', day, 'consolidated'))
    graph = client.get('/api/memory/graph').json()
    python_path = 'web/entities/tool/python.md'
    assert len(graph['edges']) == 2  # Aliases to one page count as one edge.
    python = next(n for n in graph['nodes'] if n['id'] == python_path)
    assert python['backlinks'] == 2
    assert any(n['slug'] == hash_key.split('/')[1] for n in graph['nodes'])
    assert all(e['src'].startswith('web/') and e['dst_resolved'].startswith('web/') for e in graph['edges'])
    full = client.get('/api/memory/graph', params={'include_timeline': 'true'}).json()
    assert any(n['kind'] == 'timeline' for n in full['nodes'])
    assert len(full['edges']) == 3
    overview = client.get('/api/memory/overview').json()
    assert len(overview['links']) == 2
    assert next(e for e in overview['entities'] if e['key'] == 'tool/python')['backlinks'] == 2
    facts = client.get('/api/memory/entity/project/tomo').json()['facts']
    assert doc.fact_data(facts[0])['text'] == text
    human_index = client.get('/api/memory/index')
    assert human_index.status_code == 200
    assert '[[tool/python]]' in human_index.text and 'topic/private' not in human_index.text
    # A truly empty derived index must reproduce the same API graph.
    store.with_db(lambda conn: conn.executescript('DELETE FROM vault_docs; DELETE FROM vault_aliases; DELETE FROM vault_links; DELETE FROM vault_fts;'))
    assert client.get('/api/memory/graph').json() == graph


def test_fact_endpoint_uses_authenticated_owner_and_old_api_removed(client):
    result = client.post('/api/memory/facts', json={'entity': 'user/profile', 'content': 'Prefers Indonesian.', 'user_id': 'other'})
    assert result.status_code == 200
    from app.runtime.memory.vault.notes import facts
    assert facts('web', 'user/profile') == ['Prefers Indonesian.']
    assert facts('other', 'user/profile') == []
    for endpoint in ['/api/knowledge', '/api/knowledge/upload', '/api/knowledge/kb1']:
        assert client.get(endpoint).status_code == 404
