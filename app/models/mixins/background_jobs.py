"""Durable session-owned process records and atomic completion claims."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

ACTIVE = {'starting', 'running', 'stopping', 'unknown'}
TERMINAL = {'succeeded', 'failed', 'stopped', 'interrupted'}
LOG_LIMIT = 1024 * 1024
LOG_TTL = 7 * 86400


def _decode(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = json.loads(row['payload_json'])
    logs = conn.execute('SELECT stdout,stderr FROM background_job_logs WHERE job_id=?', (data['id'],)).fetchone()
    data.update(stdout=logs['stdout'] if logs else data.get('stdout', ''),
                stderr=logs['stderr'] if logs else data.get('stderr', ''))
    if data.get('finished_at') and time.time() - data['finished_at'] > LOG_TTL and not data.get('logs_expired'):
        data.update(stdout='', stderr='', logs_expired=True, version=data['version'] + 1)
        in_transaction = conn.in_transaction
        _write(conn, data)
        if not in_transaction:
            conn.commit()
    return data


def _write(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    conn.execute(
        'UPDATE background_jobs SET status=?, continuation_status=?, monitoring_closed=?, payload_json=? WHERE id=?',
        (data['status'], data['continuation_status'], int(data['monitoring_closed']),
         json.dumps({k: v for k, v in data.items() if k not in {'stdout', 'stderr'}}), data['id']),
    )
    conn.execute('INSERT INTO background_job_logs(job_id,stdout,stderr) VALUES (?,?,?) '
                 'ON CONFLICT(job_id) DO UPDATE SET stdout=excluded.stdout,stderr=excluded.stderr',
                 (data['id'], data.get('stdout', ''), data.get('stderr', '')))


def create_background_job(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    if not data.get('session_id') or not conn.execute('SELECT 1 FROM sessions WHERE id=?', (data['session_id'],)).fetchone():
        raise ValueError('Background jobs require an existing session')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        count = conn.execute("SELECT count(*) FROM background_jobs WHERE status IN ('starting','running','stopping','unknown') AND monitoring_closed=0").fetchone()[0]
        if count >= 16:
            raise ValueError('Background job limit reached (16 active jobs)')
        item = {
            'id': 'job_' + uuid.uuid4().hex, 'session_id': '', 'user_id': '',
            'agent_id': None, 'origin_message_id': None, 'workplace_id': '',
            'backend': 'local', 'backend_handle': None, 'command': '', 'cwd': '',
            'started_at': time.time(), 'finished_at': None, 'status': 'starting',
            'returncode': None, 'stdout': '', 'stderr': '', 'truncated': False,
            'version': 1, 'log_cursor': 0, 'delivery': None, 'actor_id': None,
            'card_message_id': None, 'continuation_status': 'none',
            'delivery_status': 'local', 'result_text': '', 'monitoring_closed': False,
            **data,
        }
        conn.execute(
            'INSERT INTO background_jobs(id,session_id,status,continuation_status,monitoring_closed,payload_json) VALUES (?,?,?,?,?,?)',
            (item['id'], item['session_id'], item['status'], item['continuation_status'], int(item['monitoring_closed']),
             json.dumps({k: v for k, v in item.items() if k not in {'stdout', 'stderr'}})),
        )
        conn.execute('INSERT INTO background_job_logs(job_id,stdout,stderr) VALUES (?,?,?)',
                     (item['id'], item['stdout'], item['stderr']))
    return item


def get_background_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    return _decode(conn, conn.execute('SELECT * FROM background_jobs WHERE id=?', (job_id,)).fetchone())


def list_background_jobs(conn: sqlite3.Connection, session_id: str | None = None, *,
                         continuation_status: str | None = None, card_message_id: int | None = None,
                         delivery_status: str | None = None,
                         include_logs: bool = True, limit: int | None = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    for column, value in [('session_id', session_id), ('continuation_status', continuation_status)]:
        if value is not None:
            clauses.append(column + '=?')
            params.append(value)
    for field, value in [('card_message_id', card_message_id), ('delivery_status', delivery_status)]:
        if value is not None:
            clauses.append(f"json_extract(payload_json,'$.{field}')=?")
            params.append(value)
    projection = '*' if include_logs else "json_remove(payload_json,'$.stdout','$.stderr') AS payload_json"
    query = f'SELECT {projection} FROM background_jobs'
    if clauses:
        query += ' WHERE ' + ' AND '.join(clauses)
    query += ' ORDER BY rowid DESC'
    if limit is not None:
        query += ' LIMIT ?'
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
    if include_logs:
        return [_decode(conn, row) for row in rows]
    items = [json.loads(row['payload_json']) for row in rows]
    for item in items:
        if item.get('finished_at') and time.time() - item['finished_at'] > LOG_TTL:
            item['logs_expired'] = True
    return items


def update_background_job(conn: sqlite3.Connection, job_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    with conn:
        item = get_background_job(conn, job_id)
        if item is None:
            return None
        changes = {k: v for k, v in updates.items() if k not in {'id', 'session_id', 'user_id', 'agent_id', 'delivery', 'actor_id', 'backend', 'workplace_id', 'command', 'cwd', 'started_at'}}
        # A completed command never becomes active or emits another event.
        next_status = changes.get('status', item['status'])
        if item['status'] in TERMINAL:
            for key in ('status', 'returncode', 'finished_at', 'completion_event_id'):
                changes.pop(key, None)
        elif next_status in TERMINAL:
            changes.setdefault('finished_at', time.time())
            if (next_status in {'succeeded', 'failed'} and not item.get('continuation_suppressed')
                    and not item.get('monitoring_closed') and not changes.get('monitoring_closed')):
                changes['completion_event_id'] = f'{job_id}:completion'
                changes['continuation_status'] = 'pending'
            else:
                changes['continuation_status'] = 'cancelled'
        if 'log_cursor' not in changes and any(k in changes and changes[k] != item.get(k) for k in ('stdout', 'stderr')):
            changed_size = sum(len(str(changes.get(k, item[k]) or '').encode()) for k in ('stdout', 'stderr'))
            old_size = sum(len(str(item[k] or '').encode()) for k in ('stdout', 'stderr'))
            changes['log_cursor'] = item.get('log_cursor', 0) + max(1, changed_size - old_size)
        item.update(changes)
        out, err = str(item.get('stdout') or ''), str(item.get('stderr') or '')
        encoded_out, encoded_err = out.encode(), err.encode()
        excess = len(encoded_out) + len(encoded_err) - LOG_LIMIT
        if excess > 0:
            # Readers normally trim chronological chunks; also bound external updates.
            trim = min(excess, len(encoded_out))
            encoded_out = encoded_out[trim:]
            encoded_err = encoded_err[max(0, excess - trim):]
            item.update(stdout=encoded_out.decode(errors='ignore'), stderr=encoded_err.decode(errors='ignore'), truncated=True)
        item['version'] += 1
        _write(conn, item)
    return item


def claim_background_jobs(conn: sqlite3.Connection, ids: list[str]) -> bool:
    if not ids or len(set(ids)) != len(ids):
        return False
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        items = [get_background_job(conn, ident) for ident in ids]
        if any(item is None or item['continuation_status'] != 'pending' or item['monitoring_closed'] for item in items):
            return False
        if len({item['session_id'] for item in items}) != 1:
            return False
        if len({json.dumps([item['delivery'], item['actor_id']], sort_keys=True) for item in items}) != 1:
            return False
        for item in items:
            item.update(continuation_status='claimed', version=item['version'] + 1)
            _write(conn, item)
    return True


def set_background_jobs_paused(conn: sqlite3.Connection, sid: str, paused: bool) -> None:
    with conn:
        conn.execute('INSERT INTO background_job_admission(session_id,paused) SELECT id,? FROM sessions WHERE id=? ON CONFLICT(session_id) DO UPDATE SET paused=excluded.paused', (int(paused), sid))


def background_jobs_paused(conn: sqlite3.Connection, sid: str) -> bool:
    row = conn.execute('SELECT paused FROM background_job_admission WHERE session_id=?', (sid,)).fetchone()
    return bool(row and row[0])
