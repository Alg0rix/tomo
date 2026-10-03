"""Consolidated tests (merged from: test_background_job_manager.py, test_compact.py).
- test_background_job_manager.py: Real subprocess coverage of lifecycle and live bounded output.
- test_compact.py: /compact — session-history compaction markers.
"""

from __future__ import annotations

import asyncio
import shlex
import time
import pytest
from app.models.mixins.background_jobs import LOG_LIMIT
from app.runtime.artifacts.fs import bind_session, reset_session
from app.runtime.tools import sandbox, user_ctx
from app.services import store
from app.services.background_jobs import manager, _group_alive
from app.runtime.agent.context import history_to_messages
from app.services.compact import compact_session
from tests.fakes.llm import ScriptedLLM, text_reply


# --- from test_background_job_manager.py ---
@pytest.fixture
def sid(tmp_path):
    manager.reset()
    store.rebind(tmp_path / 'supervisor.db')
    store.update_agent('ops', {'workplace_id': ''})
    sid = store.get_or_create_session('ops', 'web')
    tokens = (bind_session(sid), sandbox.bind_agent('ops'), user_ctx.bind_user('web'))
    yield sid
    manager.reset()
    reset_session(tokens[0])
    sandbox.reset_agent(tokens[1])
    user_ctx.reset_user(tokens[2])


def wait_terminal(sid, job_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = manager.get_job(sid, job_id)
        if item['status'] in {'succeeded', 'failed', 'stopped', 'interrupted'}:
            return item
        time.sleep(0.02)
    raise AssertionError(manager.get_job(sid, job_id))


def test_multiple_jobs_survive_turn_and_capture_real_origin(sid):
    store.append_session_history(sid, {'type': 'tool_call', 'function': 'bash', 'agent_id': 'ops',
                                     'params': {'command': 'sleep .3; echo first'}})
    first = manager.start('sleep .3; echo first')
    second = manager.start('sleep .3; echo failed >&2; exit 7')
    assert first['id'] != second['id']
    assert first['origin_message_id'] is not None
    assert len(manager.list_jobs(sid)) == 2
    a, b = wait_terminal(sid, first['id']), wait_terminal(sid, second['id'])
    assert a['status'] == 'succeeded' and a['returncode'] == 0 and a['stdout'] == 'first\n'
    assert b['status'] == 'failed' and b['returncode'] == 7 and b['stderr'] == 'failed\n'
    assert a['continuation_status'] == b['continuation_status'] == 'pending'


def test_drains_running_pipes_and_combined_cap(sid, tmp_path):
    release = tmp_path / 'release'
    code = ('import os,time; from pathlib import Path; '
            'os.write(1,b"x"*1500000); os.write(2,b"y"*1500000); '
            f'print("tail",flush=True)\nwhile not Path({str(release)!r}).exists(): time.sleep(.02)')
    item = manager.start(f'python3 -c {shlex.quote(code)}')
    try:
        deadline = time.monotonic() + 10
        running = manager.get_job(sid, item['id'])
        while 'tail' not in running['stdout'] and time.monotonic() < deadline:
            time.sleep(.02)
            running = manager.get_job(sid, item['id'])
        assert 'tail' in running['stdout']
        assert running['status'] == 'running'
        assert running['log_cursor'] >= 3000000
    finally:
        release.touch()
    done = wait_terminal(sid, item['id'])
    assert done['status'] == 'succeeded'
    assert done['truncated']
    assert len(done['stdout'].encode()) + len(done['stderr'].encode()) <= LOG_LIMIT
    assert 'tail' in done['stdout']


def test_stop_group_only_and_late_stop_preserves_result(sid):
    stopped = manager.start('sleep 30 & wait')
    other = manager.start('sleep .5; echo alive')
    result = manager.stop_job(sid, stopped['id'])
    assert result['status'] == 'stopped'
    assert result['continuation_status'] == 'cancelled'
    assert not _group_alive(int(stopped['backend_handle']))
    done = wait_terminal(sid, other['id'])
    assert done['stdout'] == 'alive\n'
    assert manager.stop_job(sid, other['id'])['status'] == 'succeeded'


def test_cleanup_children_on_shell_exit(sid):
    item = manager.start('sleep 30 & echo child=$!; exit 0')
    done = wait_terminal(sid, item['id'])
    assert done['status'] == 'succeeded' and done['returncode'] == 0
    assert not _group_alive(int(item['backend_handle']))


def test_no_unbound_or_cross_session_controls(sid):
    token = bind_session(None)
    try:
        with pytest.raises(ValueError, match='authorized session'):
            manager.start('echo nope')
    finally:
        reset_session(token)
    item = manager.start('echo fine')
    other = store.get_or_create_session('ops', 'other')
    assert manager.get_job(other, item['id']) is None
    with pytest.raises(ValueError, match='not found'):
        manager.stop_job(other, item['id'])


def test_close_unknown_releases_monitor_only(sid):
    item = store.create_background_job({'session_id': sid, 'user_id': 'web', 'backend': 'tunnel', 'status': 'unknown'})
    result = manager.close_monitoring(sid, item['id'])
    assert result['status'] == 'unknown' and result['monitoring_closed']
    assert result['continuation_status'] == 'cancelled'
    complete = manager.start('echo completed')
    wait_terminal(sid, complete['id'])
    with pytest.raises(ValueError, match='Unknown'):
        manager.close_monitoring(sid, complete['id'])


def test_startup_reconciliation_and_shutdown(sid):
    old = store.create_background_job({'session_id': sid, 'user_id': 'web', 'status': 'running'})
    pending = store.create_background_job({'session_id': sid, 'user_id': 'web'})
    store.update_background_job(pending['id'], {'status': 'succeeded', 'returncode': 0})
    manager.startup()
    assert manager.get_job(sid, old['id'])['status'] == 'interrupted'
    assert manager.get_job(sid, pending['id'])['continuation_status'] == 'pending'
    assert store.background_jobs_paused(sid)
    live = manager.start('sleep 30')
    asyncio.run(manager.shutdown())
    result = manager.get_job(sid, live['id'])
    assert result['status'] == 'interrupted'
    assert not _group_alive(int(live['backend_handle']))


def test_identical_commands_keep_distinct_origin_calls(sid):
    from app.runtime.tools import progress

    for call_id in ('first-call', 'second-call'):
        store.append_session_history(sid, {'type': 'tool_call', 'function': 'bash', 'agent_id': 'ops',
                                         'call_id': call_id, 'params': {'command': 'echo repeated'}})
    jobs = []
    for call_id in ('first-call', 'second-call'):
        token = progress.bind_call_id(call_id)
        try:
            jobs.append(manager.start('echo repeated'))
        finally:
            progress.reset_call_id(token)
    assert jobs[0]['origin_message_id'] != jobs[1]['origin_message_id']
    assert jobs[0]['origin_call_id'] == 'first-call'
    for job in jobs:
        wait_terminal(sid, job['id'])


def test_clear_future_completion_suppressed_and_delete_stops(sid):
    item = manager.start('sleep .3; echo done')
    store.clear_session_by_id(sid)
    assert wait_terminal(sid, item['id'])['continuation_status'] == 'cancelled'
    live = manager.start('sleep 30')
    assert store.delete_session(sid)
    assert store.get_background_job(live['id']) is None
    assert not _group_alive(int(live['backend_handle']))


def test_remote_unknown_reconciles_original_handle_without_retry(sid, monkeypatch):
    from app.services import background_job_backends as backend
    calls = []
    monkeypatch.setattr(backend, 'resolve_backend', lambda hint: ('tunnel', 'original', '/work'))
    def start(*args):
        calls.append(args)
        raise backend.RemoteStartUnknown('disconnected', 'handle-123')
    monkeypatch.setattr(backend, 'start_remote', start)
    observed = []
    def observe(kind, workplace, handle):
        observed.append((kind, workplace, handle))
        return {'status': 'succeeded', 'exit_code': 0, 'stdout': 'remote result'}
    monkeypatch.setattr(backend, 'observe_remote', observe)
    item = manager.start('remote-command')
    assert item['status'] == 'unknown' and item['backend_handle'] == 'handle-123'
    done = wait_terminal(sid, item['id'])
    assert done['status'] == 'succeeded' and done['stdout'] == 'remote result'
    assert len(calls) == 1 and observed == [('tunnel', 'original', 'handle-123')]


def test_agent_deletion_cleans_solo_session_jobs(sid):
    live = manager.start('sleep 60')
    assert store.delete_agent('ops')
    assert store.get_session(sid) is None
    assert store.get_background_job(live['id']) is None
    assert not _group_alive(int(live['backend_handle']))


def test_deletion_waits_for_start_registration_then_stops_owned_process(sid, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    import threading

    entered, release = threading.Event(), threading.Event()
    original = manager._start_local
    def block(item):
        entered.set()
        assert release.wait(3)
        original(item)
    monkeypatch.setattr(manager, '_start_local', block)
    with ThreadPoolExecutor(max_workers=2) as pool:
        started = pool.submit(copy_context().run, manager.start, 'sleep 60')
        assert entered.wait(3)
        deleted = pool.submit(store.delete_session, sid)
        try:
            time.sleep(.02)
            assert not deleted.done()
        finally:
            release.set()
        job = started.result(timeout=5)
        assert deleted.result(timeout=5)
    assert store.get_background_job(job['id']) is None
    assert not _group_alive(int(job['backend_handle']))
    assert not manager._closing_sessions


def test_log_tail_limit_counts_utf8_bytes(sid):
    job = store.create_background_job({'session_id': sid, 'user_id': 'web',
                                       'stdout': '界' * 100, 'stderr': 'é' * 10})
    logs = manager.logs(sid, job['id'], tail=25)
    assert len(logs['stdout'].encode()) + len(logs['stderr'].encode()) <= 25


# --- from test_compact.py ---
def _seed_history(session_id: str, turns: int = 5) -> None:
    for i in range(turns):
        store.append_session_history(
            session_id, {"type": "user", "content": f"question {i}", "agent_id": "main"}
        )
        store.append_session_history(
            session_id, {"type": "final", "content": f"answer {i}", "agent_id": "main"}
        )


def _session(tmp_path) -> str:
    store.rebind(tmp_path / "compact.db")
    return store.get_or_create_session("main", "web")


def _fake_llm(monkeypatch, text: str = "Summary of earlier turns.") -> ScriptedLLM:
    llm = ScriptedLLM([text_reply(text)])
    monkeypatch.setattr("app.runtime.llm.get_llm", lambda *a, **kw: llm)
    return llm


async def test_compact_appends_marker_and_summarizes(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    _seed_history(session_id, turns=5)
    llm = _fake_llm(monkeypatch, "Talked about Q0-Q4, answered A0-A4.")

    result = await compact_session(session_id, agent_id="main")

    assert result["status"] == "ok"
    assert result["compacted"] == 10
    history = store.get_session_history(session_id)
    markers = [e for e in history if e.get("type") == "compact"]
    assert len(markers) == 1
    assert markers[0]["content"] == "Talked about Q0-Q4, answered A0-A4."
    assert llm.remaining == 0


async def test_compact_too_short_history(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    store.append_session_history(
        session_id, {"type": "user", "content": "hi", "agent_id": "main"}
    )
    llm = _fake_llm(monkeypatch)
    result = await compact_session(session_id, agent_id="main")
    assert result["status"] == "too_short"
    assert llm.remaining == 1  # no model call was spent
    assert not [e for e in store.get_session_history(session_id) if e["type"] == "compact"]


async def test_compact_no_history(tmp_path) -> None:
    session_id = _session(tmp_path)
    result = await compact_session(session_id, agent_id="main")
    assert result["status"] == "no_history"


def test_history_to_messages_respects_compact_marker() -> None:
    history = [
        {"type": "user", "content": "old q", "agent_id": "main"},
        {"type": "final", "content": "old a", "agent_id": "main"},
        {
            "type": "compact",
            "content": "We discussed old stuff.",
            "agent_id": "main",
        },
        {"type": "user", "content": "new q", "agent_id": "main"},
        {"type": "final", "content": "new a", "agent_id": "main"},
    ]
    msgs = history_to_messages(history, for_agent_id="main")
    texts = [m.get("content") for m in msgs if isinstance(m.get("content"), str)]
    assert not any("old q" in t for t in texts)
    assert not any("old a" in t for t in texts)
    assert any("compacted" in t and "We discussed old stuff." in t for t in texts)
    assert any(t == "new q" for t in texts)
    assert any(t == "new a" for t in texts)






def test_image_attachments_before_compact_are_skipped(tmp_path, monkeypatch) -> None:
    store.rebind(tmp_path / "v.db")
    from app.services import vision as svc_vision

    calls = []
    monkeypatch.setattr(store, "get_attachment", lambda aid: calls.append(aid) or {
        "id": aid, "mime_type": "image/png", "file_path": "/x.png",
    })
    history = [
        {"type": "user", "content": "old", "attachment_ids": ["img_old"]},
        {"type": "compact", "content": "s"},
        {"type": "user", "content": "new", "attachment_ids": ["img_new"]},
    ]
    out = svc_vision.collect_image_attachments(history)
    assert [a["id"] for a in out] == ["img_new"]
    assert calls == ["img_new"]


async def test_auto_compact_triggers_over_threshold(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    # Entries large enough to pass the cheap rough-size gate before the full
    # context projection is consulted.
    for i in range(8):
        store.append_session_history(
            session_id,
            {"type": "user", "content": "question " + str(i) + " " + "x" * 200, "agent_id": "main"},
        )
        store.append_session_history(
            session_id,
            {"type": "final", "content": "answer " + str(i) + " " + "y" * 200, "agent_id": "main"},
        )
    _fake_llm(monkeypatch, "Auto summary of the thread.")
    monkeypatch.setattr(
        "app.runtime.agent.context_usage._resolve_context_limit",
        lambda *a, **kw: 100,
    )
    monkeypatch.setattr(
        "app.runtime.agent.context_usage.compute_context_usage",
        lambda *a, **kw: {"used": 95, "limit": 100, "percent": 95},
    )
    store.update_settings(
        {"auto_compact_enabled": True, "auto_compact_threshold": 0.9}
    )
    from app.services.compact import maybe_auto_compact

    result = await maybe_auto_compact(session_id, agent_id="main")
    assert result is not None and result["auto"] is True
    assert result["compacted"] == 16
    assert result["usage_before"]["percent"] == 95
    assert any(
        e.get("type") == "compact" for e in store.get_session_history(session_id)
    )


async def test_auto_compact_skips_under_threshold(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    _seed_history(session_id, turns=3)
    llm = _fake_llm(monkeypatch)
    from app.services.compact import maybe_auto_compact

    assert await maybe_auto_compact(session_id, agent_id="main") is None
    assert llm.remaining == 1


async def test_auto_compact_respects_kill_switch(tmp_path, monkeypatch) -> None:
    session_id = _session(tmp_path)
    _seed_history(session_id, turns=8)
    _fake_llm(monkeypatch)
    store.update_settings({"auto_compact_enabled": False})
    monkeypatch.setattr(
        "app.runtime.agent.context_usage._resolve_context_limit",
        lambda *a, **kw: 1,
    )
    from app.services.compact import maybe_auto_compact

    assert await maybe_auto_compact(session_id, agent_id="main") is None


