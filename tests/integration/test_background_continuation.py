"""Real turn admission, process completion, and Telegram routing without networks."""

import asyncio

import httpx2
import pytest

from app.channels.telegram import TelegramAPI, TelegramDispatcher
from app.runtime.llm.base import LLMResponse, ToolCall
from app.services import background_continuation as continuation
from app.services import chat, store
from tests.fakes.llm import text_reply
from tests.fakes.access import owned_host_session
from tests.unit.channels.test_telegram_delivery import RecordingLLM
from tests.unit.channels.test_telegram_ux import Bot, message, until


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    from app.main import _register_execution_stoppers
    store.rebind(tmp_path / 'continuation.db')
    _register_execution_stoppers()
    monkeypatch.setattr('app.core.config.TOMO_WORK', tmp_path / 'work')
    monkeypatch.setattr('app.core.config.TOMO_HOME', tmp_path / 'home')
    store.update_settings({'approvals_mode': 'auto', 'learning_enabled': False})
    async def no_title(*args, **kwargs):
        return None
    monkeypatch.setattr('app.channels.web.generate_session_title', no_title)
    continuation.start()
    yield
    await continuation.stop()
    for turn in list(chat._active_turns.values()):
        if turn.task and not turn.task.done():
            turn.task.cancel()
            await asyncio.gather(turn.task, return_exceptions=True)
    chat._active_turns.clear()


def install(monkeypatch, replies):
    llm = RecordingLLM(replies)
    monkeypatch.setattr('app.runtime.agent.loop.get_llm', lambda agent_id=None, **kwargs: llm)
    return llm


def background(command):
    return LLMResponse(content=None, tool_calls=[ToolCall(
        id='background-call', name='bash', arguments={'command': command, 'background': True})])


async def run(sid, text, **kwargs):
    turn, queue = await chat.start_session_turn(sid, text, store.get_session(sid)['user_id'], **kwargs)
    turn.unsubscribe(queue)
    await turn.task
    return turn


def owned_job(sid, uid, **kwargs):
    # Durable jobs carry the owner's real execution ceiling; the drain
    # revalidates it against current policy before running anything.
    context = store.access.resolve_context(uid, sid)
    return store.create_background_job({
        'session_id': sid, 'user_id': uid,
        'execution_context': context.to_dict(), **kwargs,
    })


async def test_real_process_resumes_original_task_once_without_fake_user(setup, monkeypatch):
    sid = owned_host_session()
    llm = install(monkeypatch, [background('sleep .3; echo finished-build'),
                                text_reply('Build started.'), text_reply('Build passed.')])
    await run(sid, 'Build this project')
    await until(lambda: any(j['continuation_status'] == 'consumed' for j in store.list_background_jobs(sid)))
    job = store.list_background_jobs(sid)[0]
    history = store.get_session_history(sid)
    assert job['status'] == 'succeeded' and job['returncode'] == 0
    assert job['result_text'] == 'Build passed.'
    assert [e['content'] for e in history if e['type'] == 'user'] == ['Build this project']
    assert len([e for e in history if e['type'] == 'background_job']) == 1
    origin = next(e for e in history if e['type'] == 'tool_call')
    assert job['origin_message_id'] == origin['message_id']
    assert job['origin_call_id'] == origin['call_id']
    messages, tools = llm.calls[-1]
    assert any(m['role'] == 'tool' and 'finished-build' in str(m['content']) for m in messages)
    assert not any(t['function']['name'] == 'start_swarm' for t in tools)
    continuation.on_job_update(job)
    continuation.wake_pending()
    await asyncio.sleep(.05)
    assert len(llm.calls) == 3


async def test_busy_session_batches_pending_results_after_user_turn(setup, monkeypatch):
    sid = owned_host_session()
    uid = store.get_session(sid)['user_id']
    gate = asyncio.Event()
    entered = asyncio.Event()
    llm = install(monkeypatch, [text_reply('User turn finished.'), text_reply('Both builds passed.')])
    original = llm.complete
    async def block(messages, tools=None):
        if not entered.is_set():
            entered.set()
            await gate.wait()
        return await original(messages, tools)
    monkeypatch.setattr(llm, 'complete', block)
    turn, queue = await chat.start_session_turn(sid, 'Check these builds', uid)
    turn.unsubscribe(queue)
    await entered.wait()
    jobs = []
    for command in ['build one', 'build two']:
        job = owned_job(sid, uid, command=command)
        job = store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
        jobs.append(job)
        continuation.on_job_update(job)
    await asyncio.sleep(.03)
    assert all(store.get_background_job(j['id'])['continuation_status'] == 'pending' for j in jobs)
    gate.set()
    await turn.task
    await until(lambda: all(store.get_background_job(j['id'])['continuation_status'] == 'consumed' for j in jobs))
    assert len(llm.calls) == 2
    entries = [e for e in store.get_session_history(sid) if e['type'] == 'background_job']
    assert len(entries) == 1 and set(entries[0]['background_job_ids']) == {j['id'] for j in jobs}


async def test_stop_agent_leaves_process_running_and_pauses_until_followup(setup, monkeypatch):
    sid = owned_host_session()
    llm = install(monkeypatch, [background('sleep .3; echo survived'), text_reply('Running.'),
                                text_reply('Following up.'), text_reply('Process passed.')])
    await run(sid, 'Start build')
    assert chat.cancel_session_turn(sid)
    await until(lambda: store.list_background_jobs(sid)[0]['status'] == 'succeeded')
    await asyncio.sleep(.05)
    job = store.list_background_jobs(sid)[0]
    assert job['continuation_status'] == 'pending' and len(llm.calls) == 2
    await run(sid, 'Continue')
    await until(lambda: store.get_background_job(job['id'])['continuation_status'] == 'consumed')
    assert len(llm.calls) == 4


async def test_cancel_before_runner_starts_cannot_replay_claimed_result(setup):
    sid = owned_host_session()
    uid = store.get_session(sid)['user_id']
    job = owned_job(sid, uid)
    store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    turn, queue = await chat.start_session_turn(sid, '', uid, background_job_ids=[job['id']])
    turn.unsubscribe(queue)
    chat.cancel_session_turn(sid)
    await asyncio.gather(turn.task, return_exceptions=True)
    await asyncio.sleep(0)
    assert store.get_background_job(job['id'])['continuation_status'] == 'cancelled'
    assert not store.get_session_history(sid)


@pytest.fixture
def telegram(monkeypatch):
    bot = Bot()
    original = TelegramAPI.__init__
    def mocked(self, token, **kwargs):
        kwargs.setdefault('transport', httpx2.MockTransport(bot.transport))
        original(self, token, **kwargs)
    monkeypatch.setattr(TelegramAPI, '__init__', mocked)
    store.update_settings({'telegram_enabled': True, 'telegram_bot_token': 'test-job-bot',
                           'telegram_allowed_chat_ids': [42, -100]})
    return bot, TelegramAPI('test-job-bot')


async def test_persistent_telegram_card_and_completion_keep_old_session_after_new(setup, telegram, monkeypatch):
    bot, api = telegram
    llm = install(monkeypatch, [background('sleep 1; echo old-build-done'), text_reply('Started.'),
                                text_reply('Original build passed.')])
    dispatcher = TelegramDispatcher(api)
    update = message('Build in background', chat=-100)
    update['message']['message_thread_id'] = 8
    try:
        await dispatcher.dispatch(update)
        await until(lambda: not dispatcher.tasks)
        job = store.list_background_jobs()[0]
        jid, sid = job['id'], job['session_id']
        await until(lambda: store.get_background_job(jid).get('card_message_id'))
        card = store.get_background_job(jid)['card_message_id']
        assert bot.messages[card]['message_thread_id'] == 8
        # The turn UI is gone, but controls remain actor/topic-bound.
        for actor, topic in [(43, 8), (42, 9)]:
            await dispatcher.dispatch(bot.callback(f'tj:{jid}:stop', card, chat=-100, actor=actor, thread=topic))
            assert not store.get_background_job(jid).get('card_confirmation')
        await dispatcher.dispatch(bot.callback(f'tj:{jid}:log', card, chat=-100, thread=8))
        new = message('/new', chat=-100)
        new['message']['message_thread_id'] = 8
        await dispatcher.dispatch(new)
        await until(lambda: store.get_background_job(jid)['delivery_status'] == 'sent')
        assert store.get_background_job(jid)['session_id'] == sid
        assert len([e for e in store.get_session_history(sid) if e['type'] == 'user']) == 1
        finals = [payload for method, payload in bot.calls if method == 'sendMessage'
                  and 'Original build passed.' in payload.get('text', '')]
        assert len(finals) == 1 and finals[0]['chat_id'] == -100 and finals[0]['message_thread_id'] == 8
        assert finals[0]['reply_parameters']['message_id'] == card
        assert llm.remaining == 0
        from app.channels.telegram_jobs import session_for_reply
        explicit = message('Continue old build', chat=-100, reply_to=card)['message']
        explicit['message_thread_id'] = 8
        assert session_for_reply(explicit) == sid
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_revoked_telegram_destination_never_runs_model(setup, telegram, monkeypatch):
    bot, api = telegram
    llm = install(monkeypatch, [background('sleep .3; echo done'), text_reply('Started.')])
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message('Build'))
        await until(lambda: not dispatcher.tasks)
        store.update_settings({'telegram_enabled': False})
        await until(lambda: store.list_background_jobs()[0]['continuation_status'] == 'blocked')
        assert len(llm.calls) == 2
        assert store.list_background_jobs()[0]['delivery_status'] == 'blocked'
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_pending_restart_requires_followup_then_continues(setup, monkeypatch):
    await continuation.stop()
    sid = owned_host_session()
    job = owned_job(sid, store.get_session(sid)['user_id'])
    store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    llm = install(monkeypatch, [text_reply('Following up.'), text_reply('Recovered result.')])
    continuation.start()
    continuation.wake_pending()
    await asyncio.sleep(.03)
    assert not llm.calls and store.background_jobs_paused(sid)
    assert store.get_background_job(job['id'])['continuation_status'] == 'pending'
    await run(sid, 'Continue')
    await until(lambda: store.get_background_job(job['id'])['continuation_status'] == 'consumed')
    assert len(llm.calls) == 2


async def test_deleted_owner_blocks_pending_continuation(setup, monkeypatch):
    user = store.create_user({'username': 'deleted_owner', 'password': 'synthetic-test-password'})
    model = store.create_llm_profile({'name': 'Doomed model', 'model': 'test-model', 'api_key': 'k'})
    store.access.assign('usr_admin', user['id'], 'model', model['id'])
    sid = store.create_swarm_session(['main'], user_id=user['id'])
    job = owned_job(sid, user['id'])
    store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    # Deleting the account stops its managed work first: the supervised
    # stopper cancels the pending job, so there is nothing left to drain.
    store.delete_user(user['id'])
    assert store.get_background_job(job['id'])['continuation_status'] == 'cancelled'
    llm = install(monkeypatch, [])
    continuation.wake_pending()
    await asyncio.sleep(0.2)
    assert store.get_background_job(job['id'])['continuation_status'] == 'cancelled'
    assert not llm.calls


async def test_ambiguous_final_delivery_keeps_result_and_never_replays_model(setup, telegram, monkeypatch):
    bot, api = telegram
    transport = bot.transport
    def uncertain(request):
        if b'Background job result' in request.content:
            raise RuntimeError('Uncertain Telegram send')
        return transport(request)
    # Subsequent target-bound clients capture this replacement transport.
    bot.transport = uncertain
    llm = install(monkeypatch, [background('sleep .3; echo done'), text_reply('Started.'), text_reply('Result ready.')])
    dispatcher = TelegramDispatcher(api)
    try:
        await dispatcher.dispatch(message('Build'))
        await until(lambda: store.list_background_jobs() and store.list_background_jobs()[0]['delivery_status'] == 'unknown')
        job = store.list_background_jobs()[0]
        assert job['continuation_status'] == 'consumed' and job['result_text'] == 'Result ready.'
        continuation.wake_pending()
        await until(lambda: not dispatcher.tasks)
        assert len(llm.calls) == 3
    finally:
        await dispatcher.close()
        await api.aclose()


@pytest.mark.parametrize('followup', ['Old build followup', '/steer Old build followup'])
async def test_reply_to_old_card_queues_for_old_session_when_new_chat_busy(setup, telegram, monkeypatch, followup):
    bot, api = telegram
    llm = install(monkeypatch, [background('sleep 60'), text_reply('Started.'),
                                text_reply('New task done.'), text_reply('Old build followup done.')])
    dispatcher = TelegramDispatcher(api)
    gate, entered = asyncio.Event(), asyncio.Event()

    original = llm.complete
    async def block(messages, tools=None):
        if len(llm.calls) == 2 and not entered.is_set():
            entered.set()
            await gate.wait()
        return await original(messages, tools)
    monkeypatch.setattr(llm, 'complete', block)
    try:
        await dispatcher.dispatch(message('Build'))
        await until(lambda: not dispatcher.tasks)
        job = store.list_background_jobs()[0]
        await until(lambda: store.get_background_job(job['id']).get('card_message_id'))
        card = store.get_background_job(job['id'])['card_message_id']
        await dispatcher.dispatch(message('/new'))
        await dispatcher.dispatch(message('New unrelated task'))
        await entered.wait()
        new_sid = dispatcher.uis[42].session_id
        await dispatcher.dispatch(message(followup, reply_to=card))
        assert dispatcher.pending[42][0]['message']['_tomo_job_session'] == job['session_id']
        assert not chat.get_active_session_turn(new_sid).steer_inbox
        gate.set()
        await until(lambda: not dispatcher.tasks)
        assert [e['content'] for e in store.get_session_history(new_sid) if e['type'] == 'user'] == ['New unrelated task']
        assert [e['content'] for e in store.get_session_history(job['session_id']) if e['type'] == 'user'] == ['Build', 'Old build followup']
    finally:
        gate.set()
        await dispatcher.close()
        await api.aclose()


def captured_target(chat_id=42, topic=None):
    from app.channels.telegram import user_id_for_chat
    from app.channels.telegram_delivery import _bot_identity

    return {'channel': 'telegram', 'version': 1, 'user_id': user_id_for_chat(chat_id),
            'chat_id': chat_id, 'thread_id': topic, 'actor_id': 42, 'reply_to': 1,
            'bot': _bot_identity('test-job-bot')}


def telegram_job(target, **data):
    # Mirror production: telegram jobs execute as the designated Admin
    # principal (trusted channel ceiling) for the chat session, never as the
    # raw tg_* identity. The durable context must match the job owner.
    sid = store.get_or_create_session('main', target['user_id'], telegram_chat_id=str(target['chat_id']))
    admin_id = store.get_settings().get('telegram_execution_admin_id') or 'usr_admin'
    context = store.access.resolve_trusted_channel_context(admin_id, sid)
    return store.create_background_job({'session_id': sid, 'user_id': context.user_id,
                                       'execution_context': context.to_dict(),
                                       'delivery': target, 'actor_id': target['actor_id'], **data})


async def test_saved_pending_batch_delivers_once_on_restart_without_model(setup, telegram, monkeypatch):
    bot, api = telegram
    await continuation.stop()
    jobs = [telegram_job(captured_target(), command=command) for command in ['first', 'second']]
    for job in jobs:
        store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
        store.update_background_job(job['id'], {'continuation_status': 'consumed', 'result_text': 'Saved final.',
                                                'delivery_status': 'pending', 'result_event_id': 'one-batch'})
    llm = install(monkeypatch, [])
    continuation.start()
    await until(lambda: all(store.get_background_job(j['id'])['delivery_status'] == 'sent' for j in jobs))
    finals = [p for method, p in bot.calls if method == 'sendMessage' and 'Saved final.' in p.get('text', '')]
    assert len(finals) == 1 and not llm.calls
    await continuation.stop()
    continuation.start()
    await asyncio.sleep(.03)
    assert len([p for method, p in bot.calls if method == 'sendMessage' and 'Saved final.' in p.get('text', '')]) == 1
    await api.aclose()


async def test_two_topics_share_session_but_completion_batches_keep_destinations(setup, telegram, monkeypatch):
    bot, api = telegram
    from app.channels.telegram_jobs import update_card

    jobs = [telegram_job(captured_target(-100, topic), command=str(topic)) for topic in [8, 9]]
    assert jobs[0]['session_id'] == jobs[1]['session_id']
    llm = install(monkeypatch, [text_reply('Result one.'), text_reply('Result two.')])
    dispatcher = TelegramDispatcher(api)
    gate = asyncio.Event()
    dispatcher.tasks[-100] = asyncio.create_task(gate.wait())
    try:
        for job in jobs:
            await update_card(job['id'])
            done = store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
            continuation.on_job_update(done)
        await asyncio.sleep(.03)
        assert not llm.calls
        gate.set()
        await dispatcher.tasks[-100]
        dispatcher.tasks.pop(-100)
        continuation.wake_pending()
        await until(lambda: all(store.get_background_job(j['id'])['delivery_status'] == 'sent' for j in jobs))
        entries = [e for e in store.get_session_history(jobs[0]['session_id']) if e['type'] == 'background_job']
        assert len(entries) == 2 and all(len(e['background_job_ids']) == 1 for e in entries)
        finals = [p for method, p in bot.calls if method == 'sendMessage' and 'Background job result' in p.get('text', '')]
        assert {p['message_thread_id'] for p in finals} == {8, 9}
        for job in jobs:
            final = next(p for p in finals if job['id'][-8:] in p['text'])
            assert final['message_thread_id'] == job['delivery']['thread_id']
            assert final['reply_parameters']['message_id'] == store.get_background_job(job['id'])['card_message_id']
    finally:
        gate.set()
        await dispatcher.close()
        await api.aclose()


async def test_persistent_confirmations_expiry_cancel_repeat_and_isolated_stop(setup, telegram, monkeypatch):
    bot, api = telegram
    from app.channels import telegram_jobs

    job = telegram_job(captured_target(), status='running')
    sibling = telegram_job(captured_target(), status='running')
    unknown = telegram_job(captured_target(), status='unknown', backend='tunnel')
    calls = []
    def stop(sid, jid):
        calls.append(jid)
        return store.update_background_job(jid, {'status': 'stopped', 'returncode': -15})
    monkeypatch.setattr('app.services.background_jobs.manager.stop_job', stop)
    dispatcher = TelegramDispatcher(api)
    try:
        for item in [job, sibling, unknown]:
            await telegram_jobs.update_card(item['id'])
        async def control(item, action):
            card = store.get_background_job(item['id'])['card_message_id']
            await dispatcher.dispatch(bot.callback(f"tj:{item['id']}:{action}", card))
        await control(job, 'stop')
        assert not calls
        nonce = store.get_background_job(job['id'])['card_confirmation']['nonce']
        await control(job, 'confirm:incorrect')
        assert not calls
        store.update_background_job(job['id'], {'card_confirmation': {'nonce': nonce, 'action': 'stop', 'expires': 0}})
        await control(job, f'confirm:{nonce}')
        assert not calls
        await control(job, 'stop')
        nonce = store.get_background_job(job['id'])['card_confirmation']['nonce']
        await control(job, 'status')
        await control(job, f'confirm:{nonce}')
        assert not calls
        await control(job, 'stop')
        nonce = store.get_background_job(job['id'])['card_confirmation']['nonce']
        await control(job, f'confirm:{nonce}')
        await control(job, f'confirm:{nonce}')
        assert calls == [job['id']] and store.get_background_job(sibling['id'])['status'] == 'running'
        await control(unknown, 'close')
        nonce = store.get_background_job(unknown['id'])['card_confirmation']['nonce']
        await control(unknown, f'confirm:{nonce}')
        assert store.get_background_job(unknown['id'])['monitoring_closed']
        assert store.get_background_job(unknown['id'])['status'] == 'unknown'
        assert calls == [job['id']]
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_stop_before_continuation_dispatcher_starts_pauses_original_session(setup, telegram):
    bot, api = telegram
    from app.channels.telegram_jobs import admit_continuation

    job = telegram_job(captured_target())
    job = store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    dispatcher = TelegramDispatcher(api)
    try:
        assert admit_continuation([job])
        await dispatcher.dispatch(message('/stop'))
        assert store.background_jobs_paused(job['session_id'])
        assert store.get_background_job(job['id'])['continuation_status'] == 'pending'
        assert not store.get_session_history(job['session_id'])
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_revocation_during_continuation_ui_start_blocks_model(setup, telegram, monkeypatch):
    bot, api = telegram
    from app.channels.telegram_ui import TelegramTurnUI

    async def revoke(self):
        store.update_settings({'telegram_enabled': False})
    monkeypatch.setattr(TelegramTurnUI, 'start', revoke)
    llm = install(monkeypatch, [])
    dispatcher = TelegramDispatcher(api)
    job = telegram_job(captured_target())
    done = store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    try:
        continuation.on_job_update(done)
        await until(lambda: store.get_background_job(job['id'])['continuation_status'] == 'blocked')
        assert not llm.calls
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_relink_during_old_card_media_download_never_writes_old_session(setup, telegram, monkeypatch):
    bot, api = telegram
    from app.channels.telegram_jobs import update_card

    job = telegram_job(captured_target(), status='running')
    await update_card(job['id'])
    new_owner = store.create_user({'username': 'new_link', 'password': 'synthetic-test-password'})
    async def relink(self, file_id, **kwargs):
        monkeypatch.setattr('app.channels.telegram.user_id_for_chat', lambda chat_id: new_owner['id'])
        monkeypatch.setattr('app.channels.telegram_delivery.user_id_for_chat', lambda chat_id: new_owner['id'])
        return b'untrusted old conversation attachment'
    monkeypatch.setattr(TelegramAPI, 'download_file', relink)
    llm = install(monkeypatch, [])
    dispatcher = TelegramDispatcher(api)
    update = message('Review this', reply_to=store.get_background_job(job['id'])['card_message_id'])
    update['message'].pop('text')
    update['message']['document'] = {'file_id': 'test-file', 'file_name': 'report.txt', 'mime_type': 'text/plain'}
    update['message']['caption'] = 'Review this'
    try:
        await dispatcher.dispatch(update)
        await until(lambda: not dispatcher.tasks)
        assert not llm.calls and not store.get_session_history(job['session_id'])
        assert store.with_db(lambda conn: conn.execute('SELECT count(*) FROM attachments WHERE session_id=?',
                                                      (job['session_id'],)).fetchone()[0]) == 0
    finally:
        await dispatcher.close()
        await api.aclose()


async def test_job_deleted_during_flood_wait_blocks_http_retry(setup, telegram):
    from app.channels.telegram_jobs import JobTelegramAPI

    bot, normal_api = telegram
    job = telegram_job(captured_target())
    # Terminal locally so session teardown confirms trivially (no phantom OS
    # process to stop); the retry path under test only needs the session gone.
    store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    calls = []
    def flood(request):
        calls.append(request)
        # Deletion at this seam occurs before the retry's per-attempt check.
        store.delete_session(job['session_id'])
        return httpx2.Response(429, json={'ok': False, 'error_code': 429, 'parameters': {'retry_after': .01}})
    api = JobTelegramAPI(job['delivery'], [job['id']])
    old_client = api._client
    api._client = httpx2.AsyncClient(transport=httpx2.MockTransport(flood))
    await old_client.aclose()
    try:
        from app.channels.delivery import DeliveryBlocked

        with pytest.raises(DeliveryBlocked):
            await api.send_message(42, 'Retained private log')
        assert len(calls) == 1
    finally:
        await api.aclose()
        await normal_api.aclose()
