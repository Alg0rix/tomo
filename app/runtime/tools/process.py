"""Session- and destination-scoped controls for supervised background jobs."""
from __future__ import annotations

import json
from typing import Any


def _context() -> tuple[str, dict[str, Any] | None]:
    from app.runtime.artifacts.fs import current_session_id
    from app.runtime.tools.user_ctx import current_user_id
    from app.channels.delivery import capture_current_target
    from app.services.store import store
    sid = current_session_id()
    session = store.get_session(sid) if sid else None
    if session is None or session['user_id'] != current_user_id():
        raise ValueError('Process controls require the current authorized session')
    target = capture_current_target()
    if session.get('channel') == 'telegram' and target is None:
        raise ValueError('Telegram process controls require a bound destination')
    return sid, target


def _allowed(item: dict[str, Any], target: dict[str, Any] | None) -> bool:
    captured = item.get('delivery')
    if target is None:
        # Web can view its authenticated session, including Telegram-origin jobs.
        return True
    if not captured:
        return False
    return all(captured.get(key) == target.get(key) for key in ('channel', 'chat_id', 'thread_id', 'actor_id', 'bot', 'user_id'))


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return 'Error: process expects a dict of arguments'
    action = str(arguments.get('action') or '').strip().lower()
    if action not in {'list', 'status', 'kill', 'log', 'close-monitoring'}:
        return "Error: 'action' must be one of: list, status, kill, log, close-monitoring"
    from app.services.background_jobs import manager
    try:
        sid, target = _context()
        if action == 'list':
            jobs = [item for item in manager.list_jobs(sid) if _allowed(item, target)]
            if not jobs:
                return 'No background jobs'
            from app.services.store import store

            return '\n'.join(f"{item['id']}: {item['status']} rc={item['returncode']} backend={item['backend']} continuation={item['continuation_status']} delivery={item['delivery_status']} continuation_paused={store.background_jobs_paused(sid)} cmd={item['command']!r}" for item in jobs)
        job_id = arguments.get('id')
        if not isinstance(job_id, str) or not job_id.strip():
            return "Error: 'id' is required for status/kill/log/close-monitoring"
        job_id = job_id.strip()
        item = manager.get_job(sid, job_id)
        if item is None or not _allowed(item, target):
            return 'Error: background job not found'
        if action == 'kill':
            item = manager.stop_job(sid, job_id)
        elif action == 'close-monitoring':
            if arguments.get('confirm') is not True:
                return 'Error: closing monitoring requires explicit user intent and confirm=true; the process may still run'
            item = manager.close_monitoring(sid, job_id)
        elif action == 'log':
            logs = manager.logs(sid, job_id, tail=arguments.get('tail', 65536), cursor=arguments.get('cursor'))
            return json.dumps(logs, ensure_ascii=False)
        parts = [f"id: {item['id']}", f"status: {item['status']}",
                 f"returncode: {item['returncode']}", f"command: {item['command']}",
                 f"backend: {item['backend']}", f"continuation: {item['continuation_status']}",
                 f"monitoring_closed: {item['monitoring_closed']}"]
        from app.services.store import store

        parts.extend([f"delivery: {item['delivery_status']}",
                      f"continuation_paused: {store.background_jobs_paused(sid)}"])
        logs = manager.logs(sid, job_id)
        for kind in ('stdout', 'stderr'):
            if logs[kind]:
                parts.append(f'{kind}:\n{logs[kind].rstrip()}')
        if logs['truncated']:
            parts.append('Log tail truncated')
        if logs['logs_expired']:
            parts.append('Logs expired after seven days')
        if item.get('reason'):
            parts.append(f"reason: {item['reason']}")
        return '\n'.join(parts)
    except (ValueError, TypeError, RuntimeError) as exc:
        return f'Error: {exc}'


__all__ = ['run']
