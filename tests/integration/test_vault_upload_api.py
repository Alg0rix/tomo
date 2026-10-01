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


def test_fact_endpoint_uses_authenticated_owner_and_old_api_removed(client):
    result = client.post('/api/memory/facts', json={'entity': 'user/profile', 'content': 'Prefers Indonesian.', 'user_id': 'other'})
    assert result.status_code == 200
    from app.runtime.memory.vault.notes import facts
    assert facts('web', 'user/profile') == ['Prefers Indonesian.']
    assert facts('other', 'user/profile') == []
    for endpoint in ['/api/knowledge', '/api/knowledge/upload', '/api/knowledge/kb1']:
        assert client.get(endpoint).status_code == 404
