"""Account linking through authenticated HTTP, Bot updates and real turn/memory storage."""
from datetime import date
import hashlib

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.channels.telegram import TelegramAPI, TelegramDispatcher, process_update, user_id_for_chat
from app.channels.delivery import DeliveryBlocked, open_delivery
from app.core import config
from app.core.deps import require_auth
from app.main import app
from app.runtime.memory.vault import doc, paths
from app.runtime.memory.vault.write import add_entity, append_timeline, forget_fact
from app.services import store
from app.services import telegram_accounts
from tests.unit.channels.test_telegram_delivery import RecordingLLM, calls
from tests.unit.channels.test_telegram_ux import Bot, message, until
from tests.fakes.llm import text_reply


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'TOMO_HOME', tmp_path / 'home')
    monkeypatch.setattr(config, 'TOMO_WORK', tmp_path / 'work')
    store.rebind(tmp_path / 'linking.db')
    store.update_settings({'telegram_allowed_chat_ids': [42, 9, -100], 'learning_enabled': False,
                           'approvals_mode': 'smart', 'memory_vault_enabled': False})
    alice = store.create_user({'username': 'alice', 'password': 'password1', 'role': 'member'})
    bob = store.create_user({'username': 'bob', 'password': 'password1', 'role': 'member'})
    profile = store.create_llm_profile({'name': 'Assigned linking model', 'model': 'local-test'})
    store.set_default_llm_profile(profile['id'])
    for uid in (alice['id'], bob['id']):
        store.access.assign('usr_admin', uid, 'model', profile['id'])
    from app.runtime.supervision import stop_session as stop_turns
    from app.services.background_jobs import manager
    store.access.register_execution_stopper(stop_turns)
    store.access.register_execution_stopper(manager.stop_session)
    override = app.dependency_overrides.pop(require_auth, None)
    client = TestClient(app)
    try:
        client.post('/login', data={'username': 'alice', 'password': 'password1'})
        yield client, alice['id'], bob['id']
    finally:
        client.close()
        if override is not None:
            app.dependency_overrides[require_auth] = override


def update(code, *, chat=42, chat_type='private', sender=42):
    return {'message': {'chat': {'id': chat, 'type': chat_type}, 'from': {'id': sender},
                        'message_id': 1, 'text': '/link ' + code}}


async def test_web_dm_link_merges_memory_and_keeps_transport_separate(setup, monkeypatch):
    client, uid, bob = setup
    legacy = store.get_or_create_session('main', 'tg_42')
    web = store.get_or_create_session('main', uid)
    job = store.create_schedule({'name': 'linked-report', 'agent_id': 'main', 'schedule': 'every 1h',
                                 'message': 'Report', 'delivery_target': {
                                     'version': 1, 'channel': 'telegram', 'chat_id': 42,
                                     'bot': hashlib.sha256(b'test-token').hexdigest()}})
    store.append_session_history(legacy, {'type': 'user', 'content': 'Telegram history unique'})
    add_entity('tg_42', 'user/profile', 'Prefers spicy food.', aliases=['telegram-me'])
    add_entity('tg_42', 'user/profile', 'Old preference.')
    add_entity(uid, 'user/profile', 'Prefers spicy food.')
    add_entity(uid, 'user/profile', 'Old preference.')
    forget_fact(uid, 'user/profile', 1)
    add_entity(uid, 'user/profile', 'Lives in Jakarta.')
    add_entity(bob, 'user/profile', 'Bob secret.')
    append_timeline('tg_42', legacy, 'main', 'Legacy Telegram event')
    append_timeline(uid, web, 'main', 'Original web event')
    ep = store.insert_episode({'user_id': 'tg_42', 'session_id': legacy,
                               'title': 'Telegram experience', 'content': 'Fixed a Telegram incident'})
    code = client.post(f'/api/users/{uid}/telegram/link-code').json()['code']
    bot = Bot()
    api = TelegramAPI('test-token', transport=httpx2.MockTransport(bot.transport))
    dispatcher = TelegramDispatcher(api)
    llm = RecordingLLM([
        calls(('memory', {'action': 'add', 'entity': 'user/profile', 'content': 'Likes tea.'})),
        text_reply('Remembered.'),
    ])
    monkeypatch.setattr('app.runtime.agent.loop.get_llm', lambda agent_id=None, **kwargs: llm)
    try:
        await dispatcher.dispatch(update(code))
        assert user_id_for_chat(42) == uid
        assert client.get(f'/api/users/{uid}/telegram').json()['links'][0]['chat_id'] == '42'
        adopted = store.get_session(legacy)
        assert adopted['user_id'] == uid
        assert adopted['workplace_id'] == store.access.ensure_personal_space(uid)['id']
        assert adopted['execution_mode'] == 'restricted'
        assert adopted['additional_workplace_ids'] == []
        assert store.access.resolve_context(uid, legacy).user_id == uid
        assert store.get_session(legacy)['channel'] == 'telegram'
        assert store.get_session(legacy)['telegram_chat_id'] == '42'
        assert store.get_or_create_session('main', uid) == web
        assert store.get_or_create_session('main', uid, telegram_chat_id='42') == legacy
        assert store.get_or_create_session('main', uid, telegram_chat_id='9') not in {legacy, web}
        assert ep['id'] in {e['id'] for e in store.list_episodes(user_id=uid)}
        assert store.search_messages('Telegram history unique', user_id=uid)
        raw = client.get('/api/memory/entity/user/profile').json()['raw']
        assert 'Lives in Jakarta.' in raw and 'Prefers spicy food.' in raw
        assert len([e for e in doc.parse(raw).entries if not e.startswith('~~') and 'Old preference.' in e]) == 0
        timeline = paths.timeline_path(uid, date.today().isoformat()).read_text()
        assert 'Legacy Telegram event' in timeline and 'Original web event' in timeline
        snapshot = paths.vault_root(uid).parents[2] / 'state' / 'telegram-memory-backups' / f'tg_42-to-{uid}'
        assert 'Lives in Jakarta.' in (snapshot / 'account/entities/user/profile.md').read_text()
        assert 'Old preference.' in (snapshot / 'telegram/entities/user/profile.md').read_text()
        await dispatcher.dispatch(message('Remember that I like tea'))
        await until(lambda: not dispatcher.tasks)
        assert 'Likes tea.' in client.get('/api/memory/entity/user/profile').json()['raw']
        prompt = '\n'.join(m['content'] for m in llm.calls[0][0] if m['role'] == 'system')
        assert 'Lives in Jakarta.' in prompt and 'Bob secret.' not in prompt
        assert 'Likes tea.' not in paths.entity_path(bob, 'user/profile').read_text()
        target = store.get_schedule(job['id'])['delivery_target']
        assert target['user_id'] == uid
        store.update_settings({'telegram_enabled': True, 'telegram_bot_token': 'test-token'})
        async with open_delivery(target, 'scheduler-job-session'):
            pass
        assert client.delete(f'/api/users/{uid}/telegram/42').status_code == 200
        with pytest.raises(DeliveryBlocked):
            async with open_delivery(target, 'scheduler-job-session'):
                pytest.fail('Unlinked account must not receive old scheduled output')
        assert user_id_for_chat(42) == 'tg_42'
        assert store.get_or_create_session('main', 'tg_42') != legacy
        assert 'Likes tea.' in client.get('/api/memory/entity/user/profile').json()['raw']
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_link_codes_require_owner_private_dm_valid_account_and_single_use(setup, monkeypatch):
    client, uid, bob = setup
    assert client.post(f'/api/users/{bob}/telegram/link-code').status_code == 403
    assert client.get(f'/api/users/{bob}/telegram').status_code == 403
    assert client.delete(f'/api/users/{bob}/telegram/42').status_code == 403
    first = client.post(f'/api/users/{uid}/telegram/link-code').json()['code']
    code = client.post(f'/api/users/{uid}/telegram/link-code').json()['code']
    assert 'Invalid or expired' in (await process_update(update(first)))['reply']
    assert 'private DM' in (await process_update(update(code, chat=-100, chat_type='group')))['reply']
    assert 'private DM' in (await process_update(update(code, sender=9)))['reply']
    sid = store.get_or_create_session('main', 'tg_42')
    assert store.try_begin_session_turn(sid)
    try:
        assert 'running tasks' in (await process_update(update(code)))['reply']
        assert user_id_for_chat(42) == 'tg_42'
    finally:
        store.end_session_turn(sid)
    assert 'Linked to alice' in (await process_update(update(code)))['reply']
    assert 'Invalid or expired' in (await process_update(update(code, chat=9, sender=9)))['reply']
    from app.channels.telegram import chat_is_allowed
    store.update_user(uid, {'enabled': False})
    assert not chat_is_allowed(42)
    disabled = client.post(f'/api/users/{uid}/telegram/link-code')
    assert disabled.status_code == 401
    store.update_user(uid, {'enabled': True})
    assert client.delete(f'/api/users/{uid}/telegram/42').status_code == 200
    monkeypatch.setattr(telegram_accounts, 'CODE_TTL', -1)
    expired = client.post(f'/api/users/{uid}/telegram/link-code').json()['code']
    assert 'Invalid or expired' in (await process_update(update(expired)))['reply']
    assert user_id_for_chat(42) == 'tg_42'


def test_legacy_telegram_sessions_survive_destination_column_migration(tmp_path):
    store.rebind(tmp_path / 'legacy.db')
    sid = store.get_or_create_session('main', 'tg_42')
    store.with_db(lambda conn: conn.execute('ALTER TABLE sessions DROP COLUMN telegram_chat_id'))
    store.rebind(tmp_path / 'legacy.db')
    session = store.get_session(sid)
    assert session['user_id'] == 'tg_42'
    assert session['channel'] == 'telegram' and session['telegram_chat_id'] == '42'
    store.rebind(tmp_path / 'legacy.db')
    assert store.get_session(sid)['telegram_chat_id'] == '42'
