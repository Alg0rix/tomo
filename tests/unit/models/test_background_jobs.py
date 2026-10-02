"""Persistence, admission, retention, and completion-claim invariants."""
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from app.models.mixins.background_jobs import LOG_LIMIT
from app.services import store


@pytest.fixture
def sid(tmp_path):
    store.rebind(tmp_path / 'jobs.db')
    return store.get_or_create_session('ops', 'web')


def make(sid, **data):
    return store.create_background_job({'session_id': sid, 'user_id': 'web', 'command': 'echo hi', **data})


def test_durable_completion_claim_once(sid):
    item = make(sid)
    done = store.update_background_job(item['id'], {'status': 'succeeded', 'returncode': 0})
    assert done['continuation_status'] == 'pending'
    event = done['completion_event_id']
    store.update_background_job(item['id'], {'status': 'running'})
    assert store.get_background_job(item['id'])['status'] == 'succeeded'
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: store.claim_background_jobs([item['id']]), range(4)))
    assert claims.count(True) == 1
    store.update_background_job(item['id'], {'status': 'failed', 'returncode': 3})
    read = store.get_background_job(item['id'])
    assert read['returncode'] == 0
    assert read['continuation_status'] == 'claimed'
    assert read['completion_event_id'] == event


def test_claim_batch_atomic_and_one_destination(sid):
    first = make(sid, delivery={'thread_id': 1}, actor_id=1)
    second = make(sid, delivery={'thread_id': 2}, actor_id=1)
    for item in (first, second):
        store.update_background_job(item['id'], {'status': 'succeeded', 'returncode': 0})
    assert not store.claim_background_jobs([first['id'], second['id']])
    assert not store.claim_background_jobs([first['id'], 'missing'])
    assert store.get_background_job(first['id'])['continuation_status'] == 'pending'


def test_global_admission_atomic(sid):
    other = store.get_or_create_session('ops', 'another-user')
    def start(index):
        try:
            return make(sid if index % 2 else other)['id']
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(start, range(24)))
    assert len([ident for ident in ids if ident]) == 16
    assert len(set(ident for ident in ids if ident)) == 16
    open_job = next(ident for ident in ids if ident)
    store.update_background_job(open_job, {'status': 'unknown', 'monitoring_closed': True})
    assert make(sid)['id']


def test_bounds_expiry_and_binding_immutable(sid):
    item = make(sid)
    result = store.update_background_job(item['id'], {'stdout': 'x' * LOG_LIMIT, 'stderr': 'y' * LOG_LIMIT,
                                                      'session_id': 'malicious', 'delivery': {'chat_id': 1}})
    assert len(result['stdout'].encode()) + len(result['stderr'].encode()) <= LOG_LIMIT
    assert result['truncated']
    assert result['session_id'] == sid and result['delivery'] is None
    store.update_background_job(item['id'], {'status': 'succeeded', 'returncode': 0,
                                            'finished_at': time.time() - 8 * 86400})
    expired = store.get_background_job(item['id'])
    assert expired['logs_expired'] and expired['stdout'] == expired['stderr'] == ''


def test_stop_and_clear_never_nudge(sid):
    stopped = make(sid)
    assert store.update_background_job(stopped['id'], {'status': 'stopped', 'returncode': -15})['continuation_status'] == 'cancelled'
    active = make(sid)
    store.clear_session_by_id(sid)
    assert store.background_jobs_paused(sid)
    done = store.update_background_job(active['id'], {'status': 'succeeded', 'returncode': 0})
    assert done['continuation_status'] == 'cancelled'
    store.set_background_jobs_paused(sid, False)
    assert not store.background_jobs_paused(sid)


def test_closed_monitor_cannot_resurrect_completion(sid):
    item = make(sid, status='unknown')
    store.update_background_job(item['id'], {'monitoring_closed': True, 'continuation_status': 'cancelled'})
    done = store.update_background_job(item['id'], {'status': 'succeeded', 'returncode': 0})
    assert done['continuation_status'] == 'cancelled'
    assert not store.claim_background_jobs([item['id']])


def test_filtered_metadata_listing_omits_logs(sid):
    job = make(sid, stdout='large log', card_message_id=123)
    done = store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    metadata = store.list_background_jobs(continuation_status='pending', card_message_id=123, include_logs=False)
    assert len(metadata) == 1 and metadata[0]['id'] == done['id']
    assert 'stdout' not in metadata[0] and 'stderr' not in metadata[0]
    assert store.get_background_job(job['id'])['stdout'] == 'large log'


def test_inline_log_upgrade_preserves_then_separates_retained_output(sid):
    import json

    job = make(sid)
    def legacy(conn):
        payload = {**job, 'stdout': 'legacy output', 'stderr': 'legacy error'}
        with conn:
            conn.execute('UPDATE background_jobs SET payload_json=? WHERE id=?', (json.dumps(payload), job['id']))
            conn.execute('DELETE FROM background_job_logs WHERE job_id=?', (job['id'],))
    store.with_db(legacy)
    assert store.get_background_job(job['id'])['stdout'] == 'legacy output'
    store.update_background_job(job['id'], {'status': 'running'})
    def inspect(conn):
        payload = json.loads(conn.execute('SELECT payload_json FROM background_jobs WHERE id=?', (job['id'],)).fetchone()[0])
        logs = conn.execute('SELECT * FROM background_job_logs WHERE job_id=?', (job['id'],)).fetchone()
        assert 'stdout' not in payload and 'stderr' not in payload
        assert logs['stdout'] == 'legacy output' and logs['stderr'] == 'legacy error'
    store.with_db(inspect)


def test_session_cascade_removes_log_and_admission_rows(sid):
    job = make(sid, stdout='retained')
    store.update_background_job(job['id'], {'status': 'succeeded', 'returncode': 0})
    store.set_background_jobs_paused(sid, True)
    assert store.delete_session(sid)
    def inspect(conn):
        assert conn.execute('SELECT 1 FROM background_jobs WHERE id=?', (job['id'],)).fetchone() is None
        assert conn.execute('SELECT 1 FROM background_job_logs WHERE job_id=?', (job['id'],)).fetchone() is None
        assert conn.execute('SELECT 1 FROM background_job_admission WHERE session_id=?', (sid,)).fetchone() is None
    store.with_db(inspect)


def test_multibyte_truncation_stays_within_byte_limit(sid):
    job = make(sid)
    result = store.update_background_job(job['id'], {'stdout': '界' * LOG_LIMIT, 'stderr': 'é'})
    assert len(result['stdout'].encode()) + len(result['stderr'].encode()) <= LOG_LIMIT
