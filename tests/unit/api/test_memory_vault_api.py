from __future__ import annotations

from fastapi.testclient import TestClient
from app.core import config
from app.core.deps import require_auth
from app.main import app
from app.runtime.memory.vault.write import add_entity, record_turn
from app.runtime.memory.vault.paths import timeline_path
from datetime import date
from app.services import store


def test_memory_graph_entity_timeline_and_forget(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'TOMO_HOME', tmp_path)
    store.rebind(tmp_path / 'vault.db')
    agent_id = store.get_coordinator()['id']
    session_id = store.get_or_create_session(agent_id, 'web')
    record_turn(session_id, agent_id, 'Before enabling', 'No timeline yet')
    assert not timeline_path('web', date.today().isoformat()).exists()
    store.update_settings({'memory_vault_enabled': True})
    add_entity('web', 'project/tomo', 'Tomo uses [[tool/python]].')
    add_entity('web', 'tool/python', 'Python is a language.')
    add_entity('other', 'person/secret', 'Hidden fact.')
    record_turn(session_id, agent_id, 'Tomo uses Python', 'Recorded in the vault')
    app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(app)
        graph = client.get('/api/memory/graph')
        assert graph.status_code == 200
        data = graph.json()
        assert {n['slug'] for n in data['nodes']} == {'tomo', 'python'}
        assert len(data['edges']) == 1
        day = data['days'][0]
        timeline = client.get('/api/memory/timeline', params={'date': day})
        assert timeline.status_code == 200
        assert timeline.json()['blocks']
        entity = client.get('/api/memory/entity/project/tomo')
        assert 'Tomo uses' in entity.json()['raw']
        assert client.post('/api/memory/entity/project/tomo/forget', json={'number': 0}).status_code == 200
        assert client.get('/api/memory/entity/project/tomo').json()['facts'][0].startswith('~~')
        assert client.get('/api/memory/entity/person/secret').status_code == 404
        assert client.get('/memory').status_code == 200
    finally:
        app.dependency_overrides.pop(require_auth, None)
