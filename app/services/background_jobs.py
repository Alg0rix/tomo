"""Application-owned supervisors for bounded, session-bound background commands."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
import codecs
from collections import deque
import logging
import os
from pathlib import Path
import signal
import select
import subprocess
import threading
import time
from typing import Any, Callable

from app.models.mixins.background_jobs import ACTIVE, LOG_LIMIT, TERMINAL
from app.services.store import store

_logger = logging.getLogger(__name__)


class _Local:
    def __init__(self, proc: subprocess.Popen, environment: Any, duration_seconds: int):
        self.proc = proc
        self.environment = environment
        # Captured at fenced admission. Observation/termination must still work
        # after revocation marks policy pending; it is not a new tool action.
        self.deadline = time.monotonic() + duration_seconds
        self.lock = threading.RLock()
        self.chunks: deque[tuple[str, str, int]] = deque()
        self.size = 0
        self.cursor = 0
        self.truncated = False
        self.stop_requested = False
        self.interrupted = False
        self.readers_closed = threading.Event()
        self.thread: threading.Thread | None = None

    def append(self, stream: str, text: str) -> None:
        size = len(text.encode())
        with self.lock:
            self.chunks.append((stream, text, size))
            self.size += size
            self.cursor += size
            while self.size > LOG_LIMIT and self.chunks:
                kind, value, count = self.chunks.popleft()
                excess = self.size - LOG_LIMIT
                if count > excess:
                    value = value.encode()[excess:].decode(errors='ignore')
                    remaining = len(value.encode())
                    self.chunks.appendleft((kind, value, remaining))
                    self.size -= count - remaining
                else:
                    self.size -= count
                self.truncated = True

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                'stdout': ''.join(chunk for kind, chunk, _ in self.chunks if kind == 'stdout'),
                'stderr': ''.join(chunk for kind, chunk, _ in self.chunks if kind == 'stderr'),
                'log_cursor': self.cursor, 'truncated': self.truncated,
            }


def _group_alive(pid: int) -> bool:
    # Ignore reparented zombies on Linux: they cannot execute or retain pipes.
    if Path('/proc').is_dir():
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if int(stat[2]) == pid and stat[0] != 'Z':
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass


def _clean_group(pid: int, grace: float = 0.4) -> bool:
    if not _group_alive(pid):
        return True
    _signal_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and _group_alive(pid):
        time.sleep(0.03)
    if _group_alive(pid):
        _signal_group(pid, signal.SIGKILL)
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline and _group_alive(pid):
        time.sleep(0.03)
    return not _group_alive(pid)


class BackgroundJobManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local: dict[str, _Local] = {}
        self._remote_threads: dict[str, threading.Thread] = {}
        self._stopping = threading.Event()
        self._callback: Callable[[dict[str, Any]], Any] | None = None
        self._started = False
        self._closing_sessions: set[str] = set()

    @contextmanager
    def closing_session(self, sid: str):
        """Exclude startup through registration while a session is removed."""
        with self._lock:
            self._closing_sessions.add(sid)
        try:
            yield
        finally:
            with self._lock:
                self._closing_sessions.discard(sid)

    def _publish(self, job_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        previous = store.get_background_job(job_id)
        if previous and previous['status'] in TERMINAL and changes.get('status') in ACTIVE:
            changes = {k: v for k, v in changes.items() if k != 'status'}
        item = store.update_background_job(job_id, changes)
        if item and item.get('execution_context') and previous and previous['status'] != item['status']:
            saved = item['execution_context']
            store.access.audit(saved['user_id'], 'background.state', session_id=item['session_id'],
                               agent_id=saved['agent_id'], destination_id=saved['destination_id'],
                               job_id=job_id, outcome=item['status'])
        if item and self._callback:
            try:
                self._callback(item)
            except Exception:
                _logger.exception('Background job update callback failed')
        return item

    def startup(self, loop=None, on_update=None) -> None:
        """Reconcile stale observations; callbacks may originate from any thread."""
        with self._lock:
            if self._started:
                self._callback = on_update
                return
            self._started = True
            self._callback = on_update
            self._stopping.clear()
            for item in store.list_background_jobs():
                updates: dict[str, Any] = {}
                if item['status'] in ACTIVE and not item['monitoring_closed']:
                    updates.update(status='interrupted' if item['backend'] == 'local' else 'unknown', reason='Application supervisor restarted')
                if item['continuation_status'] in {'pending', 'claimed'}:
                    updates['continuation_status'] = ('cancelled' if item['continuation_status'] == 'claimed' else 'pending')
                    store.set_background_jobs_paused(item['session_id'], True)
                if item['delivery_status'] == 'sending':
                    updates['delivery_status'] = 'unknown'
                if updates:
                    self._publish(item['id'], updates)
                # Remote handles survive independently; observation does not replay commands.
                if item['backend'] != 'local' and item['status'] in ACTIVE and item.get('backend_handle') and not item['monitoring_closed']:
                    store.set_background_jobs_paused(item['session_id'], True)
                    try:
                        from app.runtime.policy import durable_context
                        durable_context(item)
                    except PermissionError:
                        self._publish(item['id'], {'status': 'unknown', 'reason': 'Stored execution identity is unavailable', 'continuation_status': 'blocked'})
                    else:
                        self._watch_remote(item['id'])

    def _origin(self, sid: str, command: str, agent_id: str | None) -> int | None:
        from app.runtime.tools.progress import current_call_id

        call_id = current_call_id()
        def lookup(conn):
            rows = conn.execute("SELECT id,params_json FROM messages WHERE session_id=? AND type='tool_call' AND function='bash' AND agent_id IS ? ORDER BY id DESC", (sid, agent_id)).fetchall()
            import json
            for row in rows:
                try:
                    params = json.loads(row['params_json'] or '{}')
                except (ValueError, TypeError):
                    continue
                if (isinstance(params, dict) and params.get('command') == command
                        and (not call_id or (params.get('__meta') or {}).get('call_id') == call_id)):
                    return row['id']
            return None
        return store.with_db(lookup)

    def start(self, command: str, workplace_hint: str | None = None) -> dict[str, Any]:
        from app.runtime.policy import authorize_tool
        context = authorize_tool('bash', {'workplace': workplace_hint})
        with store.access.execution_guard(context):
            with self._lock:
                return self._start(command, workplace_hint)

    def _start(self, command: str, workplace_hint: str | None = None) -> dict[str, Any]:
        from app.runtime.tools.progress import current_call_id
        from app.channels.delivery import capture_current_target
        from app.services import background_job_backends as backends

        from app.runtime.access import current_execution
        execution = store.access.revalidate(current_execution())
        sid = execution.session_id
        if sid in self._closing_sessions:
            raise ValueError('Background commands cannot start while their session is closing')
        session = store.get_session(sid) if sid else None
        if session is None or session['user_id'] != execution.user_id:
            raise ValueError('Background commands require the current authorized session')
        if not isinstance(command, str) or not command.strip():
            raise ValueError('Command must be a non-empty string')
        if self._stopping.is_set():
            raise ValueError('Background supervisor is shutting down')
        delivery = capture_current_target()
        if session.get('channel') == 'telegram' and delivery is None:
            raise ValueError('Telegram background commands require a bound channel destination')
        backend, workplace_id, cwd = backends.resolve_backend(workplace_hint)
        if backend == 'ssh':
            raise PermissionError('SSH background execution has no destination-side enforcement; use synchronous SSH exec or a tunnel destination')
        if backend not in {'local', 'container', 'tunnel'}:
            raise PermissionError('Remote background backend has no verified supervised execution boundary')
        aid = execution.agent_id
        item = store.create_background_job({
            'session_id': sid, 'user_id': execution.user_id, 'agent_id': aid,
            'execution_context': execution.to_dict(),
            'origin_message_id': self._origin(sid, command, aid),
            'origin_call_id': current_call_id(),
            'backend': backend, 'workplace_id': workplace_id or '', 'cwd': cwd,
            'command': command, 'delivery': delivery,
            'actor_id': delivery.get('actor_id') if delivery else None,
        })
        store.access.audit(execution.user_id, 'background.start', session_id=sid, agent_id=aid,
                           destination_id=execution.destination_id, job_id=item['id'], outcome='admitted')
        try:
            if backend == 'local':
                self._start_local(item)
            else:
                try:
                    result = backends.start_remote(backend, workplace_id, command, cwd)
                except Exception as exc:
                    handle = getattr(exc, 'backend_handle', None) or getattr(exc, 'handle', None)
                    if handle:
                        self._publish(item['id'], {'status': 'unknown', 'backend_handle': handle, 'reason': str(exc)})
                        self._watch_remote(item['id'])
                        return store.get_background_job(item['id'])
                    raise
                self._publish(item['id'], self._remote_changes(result))
                self._watch_remote(item['id'])
        except Exception as exc:
            self._publish(item['id'], {'status': 'failed', 'reason': str(exc), 'stderr': str(exc)})
        item = store.get_background_job(item['id'])
        # Start failures remain durable and observable, with no local fallback.
        return item

    def _start_local(self, item: dict[str, Any]) -> None:
        from app.runtime.policy import durable_context
        context = durable_context(item)
        if context.execution_mode == 'restricted':
            raise PermissionError('Restricted background execution requires the container backend')
        if context.role == 'admin':
            from app.services.secret_store import shell_environment
            environment = shell_environment(context.quota.duration_seconds, work_root=item['cwd'])
        else:
            environment = nullcontext({'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': item['cwd'], 'LANG': 'C.UTF-8'})
        env = environment.__enter__()
        try:
            from app.runtime.isolation import host
            proc = host.popen(['bash', '-lc', item['command']], cwd=item['cwd'], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
        except Exception:
            environment.__exit__(None, None, None)
            raise
        state = _Local(proc, environment, context.quota.duration_seconds)
        with self._lock:
            self._local[item['id']] = state
        try:
            self._publish(item['id'], {'status': 'running', 'backend_handle': str(proc.pid)})
            thread = threading.Thread(target=self._supervise_local, args=(item['id'], state), daemon=True, name=f'background-{item["id"]}')
            state.thread = thread
            thread.start()
        except Exception:
            _clean_group(proc.pid)
            proc.wait(timeout=2)
            environment.__exit__(None, None, None)
            with self._lock:
                self._local.pop(item['id'], None)
            raise

    @staticmethod
    def _reader(pipe, state: _Local, stream: str) -> None:
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while not state.readers_closed.is_set():
                try:
                    if not select.select([pipe], [], [], 0.1)[0]:
                        continue
                    data = os.read(pipe.fileno(), 8192)
                except (OSError, ValueError):
                    data = b''
                text = decoder.decode(data, final=not data)
                if text:
                    state.append(stream, text.replace('\r\n', '\n').replace('\r', '\n'))
                if not data:
                    break
        finally:
            pipe.close()

    def _supervise_local(self, job_id: str, state: _Local) -> None:
        readers = [threading.Thread(target=self._reader, args=(state.proc.stdout, state, 'stdout'), daemon=True),
                   threading.Thread(target=self._reader, args=(state.proc.stderr, state, 'stderr'), daemon=True)]
        for reader in readers:
            reader.start()
        last_cursor = -1
        try:
            while state.proc.poll() is None:
                if time.monotonic() >= state.deadline:
                    state.interrupted = True
                    _clean_group(state.proc.pid)
                    break
                snapshot = state.snapshot()
                if snapshot['log_cursor'] != last_cursor:
                    self._publish(job_id, snapshot)
                    last_cursor = snapshot['log_cursor']
                time.sleep(0.15)
            code = state.proc.wait()
            cleaned = _clean_group(state.proc.pid)
            for reader in readers:
                reader.join(0.6)
            # Readers cannot hold up completion indefinitely (unsupported escaped daemon).
            if any(reader.is_alive() for reader in readers):
                cleaned = False
                state.readers_closed.set()
                for reader in readers:
                    reader.join(0.2)
            with state.lock:
                status = ('interrupted' if state.interrupted else 'stopped'
                          if state.stop_requested and (code < 0 or code in {137, 143})
                          else 'succeeded' if code == 0 else 'failed')
            if not cleaned:
                status = 'unknown'
            changes = {**state.snapshot(), 'status': status, 'returncode': code,
                       'last_observed_at': time.time()}
            if status == 'unknown':
                changes['reason'] = 'Process-group cleanup or pipe closure could not be confirmed'
            self._publish(job_id, changes)
        except Exception:
            _logger.exception('Background process supervisor failed')
            _clean_group(state.proc.pid)
            self._publish(job_id, {'status': 'unknown', 'reason': 'Local supervisor observation failed'})
        finally:
            from app.runtime.isolation import host
            host.forget(state.proc)
            state.environment.__exit__(None, None, None)
            with self._lock:
                self._local.pop(job_id, None)

    def _remote_changes(self, result: dict[str, Any]) -> dict[str, Any]:
        status = result.get('status', 'unknown')
        code = result.get('returncode', result.get('exit_code'))
        if status in {'exited', 'completed'}:
            status = 'succeeded' if code == 0 else 'failed' if code is not None else 'unknown'
        if status in {'succeeded', 'failed'} and code is None:
            status = 'unknown'
        changes = {k: v for k, v in result.items() if k in {'stdout', 'stderr', 'truncated', 'reason', 'backend_handle', 'monitoring_closed', 'log_cursor', 'stop_confirmed'}}
        changes.update(status=status, returncode=code, last_observed_at=time.time())
        return changes

    def _watch_remote(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._remote_threads:
                return
            thread = threading.Thread(target=self._supervise_remote, args=(job_id,), daemon=True, name=f'background-remote-{job_id}')
            self._remote_threads[job_id] = thread
            thread.start()

    def _supervise_remote(self, job_id: str) -> None:
        from app.services import background_job_backends as backends
        delay = 0.5
        try:
            while not self._stopping.wait(delay):
                item = store.get_background_job(job_id)
                if item is None or item['status'] in TERMINAL or item['monitoring_closed']:
                    return
                handle = item.get('backend_handle')
                if not handle:
                    self._publish(job_id, {'status': 'unknown', 'reason': 'Remote handle was not confirmed'})
                    return
                try:
                    from app.runtime.policy import durable_context
                    from app.runtime.access import execution_scope
                    with execution_scope(durable_context(item)):
                        # Server-side duration bound: the destination enforces
                        # its own timeout too, but a hung or lying destination
                        # must not run managed work past the owner's quota.
                        try:
                            from app.runtime.access import current_execution
                            quota = current_execution().quota.duration_seconds
                            if quota and time.time() - float(item.get('started_at') or 0) > quota:
                                backends.stop_remote_job(item)
                                self._publish(job_id, {'status': 'stopped', 'reason': 'Duration quota exceeded at destination supervision'})
                                return
                        except Exception:
                            pass
                        result = backends.observe_remote(item['backend'], item['workplace_id'], handle)
                    changes = self._remote_changes(result)
                    if item.get('stop_requested') and changes['status'] in {'succeeded', 'failed'} and changes.get('returncode', 0) < 0:
                        changes['status'] = 'stopped'
                    self._publish(job_id, changes)
                    delay = min(delay * 2, 15) if changes['status'] == 'unknown' else 1.0
                except Exception as exc:
                    self._publish(job_id, {'status': 'unknown', 'reason': str(exc)})
                    delay = min(delay * 2, 15)
        finally:
            with self._lock:
                self._remote_threads.pop(job_id, None)

    def list_jobs(self, session_id: str, *, user_id: str | None = None) -> list[dict[str, Any]]:
        from app.runtime.policy import require_owned_service
        owner = require_owned_service(session_id, user_id=user_id)
        return [j for j in store.list_background_jobs(session_id, include_logs=False) if j['user_id'] == owner]

    def get_job(self, session_id: str, job_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
        from app.runtime.policy import require_owned_service
        owner = require_owned_service(session_id, user_id=user_id)
        item = store.get_background_job(job_id)
        if item and item['user_id'] != owner:
            return None
        return item if item and item['session_id'] == session_id and store.get_session(session_id) else None

    def stop_job(self, session_id: str, job_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        item = self.get_job(session_id, job_id, user_id=user_id)
        return self._stop_job(item, job_id, session_id)

    def _stop_job(self, item, job_id, session_id) -> dict[str, Any]:
        if item is None:
            raise ValueError('Background job not found')
        if item['status'] in TERMINAL:
            return item
        with self._lock:
            state = self._local.get(job_id)
        if item['backend'] == 'local':
            if state is None:
                return self._publish(job_id, {'status': 'unknown', 'reason': 'Local process is no longer owned by this supervisor'})
            with state.lock:
                if state.proc.poll() is not None:
                    # Natural completion won the race; wait briefly for final drain.
                    pass
                else:
                    state.stop_requested = True
                    self._publish(job_id, {'status': 'stopping', 'stop_requested': True})
                    _signal_group(state.proc.pid, signal.SIGTERM)
            if state.stop_requested:
                try:
                    state.proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    _signal_group(state.proc.pid, signal.SIGKILL)
            if state.thread:
                state.thread.join(1.5)
            return store.get_background_job(job_id)
        from app.services import background_job_backends as backends
        self._publish(job_id, {'status': 'stopping', 'stop_requested': True})
        try:
            # Stored owner/session identity: killing must work after the
            # live ceiling moved (revocation stops run post-bump).
            result = backends.stop_remote_job(item)
            changes = self._remote_changes(result)
        except Exception as exc:
            changes = {'status': 'unknown', 'reason': str(exc)}
        return self._publish(job_id, changes)

    def close_monitoring(self, session_id: str, job_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        item = self.get_job(session_id, job_id, user_id=user_id)
        if item is None:
            raise ValueError('Background job not found')
        if item['status'] != 'unknown':
            raise ValueError('Only Unknown jobs can close monitoring; the process may still run')
        return self._publish(job_id, {'monitoring_closed': True, 'continuation_status': 'cancelled'})

    def logs(self, session_id: str, job_id: str, tail: int = 65536, cursor: int | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        item = self.get_job(session_id, job_id, user_id=user_id)
        if item is None:
            raise ValueError('Background job not found')
        limit = max(1, min(int(tail), LOG_LIMIT))
        out, err = item['stdout'], item['stderr']
        if cursor is not None and int(cursor) == item.get('log_cursor', 0):
            out = err = ''
        else:
            err_bytes = err.encode()[-limit:]
            err = err_bytes.decode(errors='ignore')
            remaining = max(0, limit - len(err.encode()))
            out = out.encode()[-remaining:].decode(errors='ignore') if remaining else ''
        return {'id': job_id, 'stdout': out, 'stderr': err, 'cursor': item.get('log_cursor', 0),
                'truncated': item['truncated'], 'logs_expired': item.get('logs_expired', False),
                'version': item['version'], 'status': item['status']}

    def stop_session(self, session_id: str) -> None:
        """Confirmed teardown; failure leaves the policy's barrier pending."""
        from app.runtime.access import AccessUnavailable
        for item in store.list_background_jobs(session_id):
            self._publish(item['id'], {'continuation_suppressed': True, 'continuation_status': 'cancelled', 'delivery_status': 'blocked'})
            if item['status'] in ACTIVE:
                if item['backend'] == 'container':
                    from app.runtime.isolation import jobs
                    jobs.stop_session(session_id)
                    self._publish(item['id'], {'status': 'stopped', 'returncode': -1})
                    continue
                stopped = self._stop_job(item, item['id'], session_id)
                if not stopped or stopped['status'] not in TERMINAL:
                    raise AccessUnavailable('Background termination could not be confirmed')

    async def shutdown(self) -> None:
        self._stopping.set()
        for item in store.list_background_jobs():
            if item['backend'] == 'container' and item['status'] in ACTIVE:
                from app.runtime.isolation import jobs
                jobs.stop_session(item['session_id'])
        with self._lock:
            locals_ = list(self._local.items())
            remote_threads = list(self._remote_threads.values())
        def finish():
            for job_id, state in locals_:
                with state.lock:
                    state.interrupted = state.proc.poll() is None
                _clean_group(state.proc.pid)
                if state.thread:
                    state.thread.join(2)
                item = store.get_background_job(job_id)
                if item and item['status'] not in TERMINAL:
                    self._publish(job_id, {'status': 'interrupted', 'reason': 'Application shutdown'})
                elif item:
                    # Shutdown cancellation is not a failed user command completion.
                    self._publish(job_id, {'continuation_status': 'blocked'})
                if item:
                    store.set_background_jobs_paused(item['session_id'], True)
            for thread in remote_threads:
                thread.join(0.2)
        await asyncio.to_thread(finish)
        self._callback = None
        self._started = False

    def reset(self) -> None:
        """Test-only deterministic cleanup; never leave child processes behind."""
        self._stopping.set()
        self._callback = None
        for item in store.list_background_jobs():
            if item['backend'] == 'container' and item['status'] in ACTIVE:
                from app.runtime.isolation import jobs
                jobs.stop_session(item['session_id'])
        with self._lock:
            locals_ = list(self._local.values())
            remotes = list(self._remote_threads.values())
        for state in locals_:
            _clean_group(state.proc.pid, grace=0.1)
            if state.thread:
                state.thread.join(2)
        for thread in remotes:
            thread.join(0.5)
        self._started = False
        self._stopping.clear()


manager = BackgroundJobManager()
