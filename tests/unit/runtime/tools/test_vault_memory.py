import pytest

from app.runtime.tools.registry import execute, ToolRegistry
from app.runtime.tools.user_ctx import bind_user, reset_user


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from app.core import config
    from app.services import store
    monkeypatch.setattr(config, 'TOMO_HOME', tmp_path)
    store.rebind(tmp_path / 'tools.sqlite')
    token = bind_user('alice')
    yield
    reset_user(token)


def test_tool_contract_has_only_vault_properties():
    schemas = ToolRegistry().get_openai_tools()
    schema = next(s['function']['parameters'] for s in schemas if s['function']['name'] == 'memory')
    assert 'target' not in schema['properties']
    assert {'list', 'search', 'add', 'replace', 'remove'} == set(schema['properties']['action']['enum'])
    assert not {'remember', 'recall', 'forget_memory'} & set(ToolRegistry().names())


def test_large_save_search_replace_and_remove():
    content = 'Wazzapi uses Docker. ' + 'Details. ' * 700
    assert execute('memory', {'action': 'add', 'entity': 'project/wazzapi', 'content': content}) == 'Saved vault fact.'
    assert content in execute('memory', {'action': 'list', 'entity': 'project/wazzapi'})
    assert 'Wazzapi' in execute('memory', {'action': 'search', 'query': 'Wazzapi Docker'})
    assert execute('memory', {'action': 'replace', 'entity': 'project/wazzapi', 'old': 'uses Docker', 'content': 'Wazzapi uses Kubernetes.'}) == 'Replaced vault fact.'
    assert execute('memory', {'action': 'remove', 'entity': 'project/wazzapi', 'old': 'uses Kubernetes'}) == 'Removed vault fact.'
    from app.runtime.memory.vault.notes import facts
    assert facts('alice', 'project/wazzapi') == []


def test_memory_and_agent_info_show_related_pages():
    from app.runtime.memory.vault import write
    from app.runtime.memory.vault.notes import scoped_key

    execute('memory', {'action': 'add', 'entity': 'tool/python', 'content': 'An implementation language.'})
    write.add_entity('alice', scoped_key('agent', 'ops'), 'Python is used for automation.')
    assert 'Related: [[tool/python]]' in execute('memory', {'action': 'list', 'entity': scoped_key('agent', 'ops')})
    assert 'Related: [[tool/python]]' in execute('memory', {'action': 'list'})
    assert 'Related: [[tool/python]]' in execute('memory', {'action': 'search', 'query': 'automation'})
    assert 'Related: [[tool/python]]' in execute('agent_info', {'action': 'get', 'agent': 'ops', 'include': 'memory'})


def test_accounts_cannot_read_or_modify_other_accounts():
    execute('memory', {'action': 'add', 'entity': 'user/profile', 'content': 'Alice prefers concise replies.'})
    token = bind_user('bob')
    try:
        assert 'Alice' not in execute('memory', {'action': 'list'})
        assert 'Alice' not in execute('memory', {'action': 'search', 'query': 'Alice'})
        assert execute('memory', {'action': 'remove', 'entity': 'user/profile', 'old': 'Alice'}).startswith('Error:')
    finally:
        reset_user(token)
    assert 'Alice prefers' in execute('memory', {'action': 'list', 'entity': 'user/profile'})


def test_ambiguous_substring_and_missing_supersedes_are_errors():
    for text in ['Server alpha uses port 8000.', 'Server beta uses port 9000.']:
        execute('memory', {'action': 'add', 'entity': 'tool/server', 'content': text})
    assert execute('memory', {'action': 'remove', 'entity': 'tool/server', 'old': 'Server'}).startswith('Error:')
    assert execute('memory', {'action': 'add', 'entity': 'tool/server', 'content': 'New fact.', 'supersedes': 'Missing'}).startswith('Error:')
    assert execute('memory', {'action': 'add', 'target': 'memory', 'content': 'Legacy write.'}).startswith('Error:')


def test_session_search_still_recalls_history_and_scopes_accounts():
    from app.services import store
    sid = store.create_swarm_session(['main'], user_id='alice')
    store.append_session_history(sid, {'type': 'user', 'content': 'Investigate wazzapi deployment commit.'})
    assert sid in execute('session_search', {'query': 'wazzapi deployment'})
    assert 'no matching' in execute('memory', {'action': 'search', 'query': 'wazzapi deployment'}).lower()
    token = bind_user('bob')
    try:
        assert sid not in execute('session_search', {'query': 'wazzapi deployment'})
    finally:
        reset_user(token)


def test_memory_is_mutating_for_parallel_tool_scheduling():
    from app.runtime.agent.atg.interfaces import is_read_only
    assert not is_read_only('memory')
