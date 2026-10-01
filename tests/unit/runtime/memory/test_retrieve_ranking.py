from app.runtime.memory.retrieve import retrieve_for_turn
from app.runtime.memory.vault import write
from app.runtime.memory.vault.notes import scoped_key


def test_retrieval_uses_vault_profile_project_and_entities(tmp_path, monkeypatch):
    from app.core import config
    from app.services import store
    monkeypatch.setattr(config, 'TOMO_HOME', tmp_path)
    store.rebind(tmp_path / 'db.sqlite')
    store.create_agent({'id': 'dev', 'name': 'Dev', 'workplace_id': 'wp1'})
    write.add_entity('web', 'user/profile', 'Prefers short answers.')
    write.add_entity('web', scoped_key('project', 'wp1'), 'Stack is FastAPI and SQLite.')
    write.add_entity('web', 'tool/widget', 'Widget deploys require approval.')
    block = retrieve_for_turn('widget deploy preferences', agent_id='dev')
    assert 'Prefers short answers.' in block
    assert 'FastAPI' in block
    assert 'Widget deploys require approval.' in block
