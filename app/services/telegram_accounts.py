"""Verified Telegram DM identities linked to login accounts.

Session ownership becomes the account id; the separate Telegram destination
stays on the session. Link codes are random, hashed, short-lived and single-use.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time

from app.services.store import store

CODE_TTL = 600


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def linked_account(chat_id: int | str) -> dict | None:
    return store.with_db(lambda conn: _linked_account(conn, str(int(chat_id))))


def _linked_account(conn, chat_id: str) -> dict | None:
    row = conn.execute(
        "SELECT u.id, u.username, u.enabled FROM telegram_account_links l "
        "JOIN users u ON u.id=l.user_id WHERE l.chat_id=?", (chat_id,),
    ).fetchone()
    return dict(row) if row else None


def user_id_for_chat(chat_id: int | str) -> str:
    chat_id = str(int(chat_id))
    account = linked_account(chat_id)
    return account['id'] if account else f'tg_{chat_id}'


def list_links(user_id: str) -> list[dict]:
    return store.with_db(lambda conn: [dict(row) for row in conn.execute(
        "SELECT chat_id, created_at FROM telegram_account_links WHERE user_id=? ORDER BY created_at",
        (user_id,),
    )])


def create_code(user_id: str) -> dict:
    code = secrets.token_urlsafe(16)
    expires_at = time.time() + CODE_TTL

    def create(conn):
        user = store.get_user(user_id)
        if not user or not user['enabled']:
            raise ValueError('Account not found or disabled')
        conn.execute('DELETE FROM telegram_link_codes WHERE expires_at<=?', (time.time(),))
        conn.execute(
            'INSERT INTO telegram_link_codes (code_hash,user_id,expires_at) VALUES (?,?,?) '
            'ON CONFLICT(user_id) DO UPDATE SET code_hash=excluded.code_hash, expires_at=excluded.expires_at',
            (_hash(code), user_id, expires_at),
        )
        conn.commit()
        return {'code': code, 'command': f'/link {code}', 'expires_at': expires_at}

    return store.with_db(create)


def _require_idle(conn, user_ids: list[str], chat_id: str) -> None:
    marks = ','.join('?' for _ in user_ids)
    sessions = conn.execute(f'SELECT id FROM sessions WHERE user_id IN ({marks})', user_ids).fetchall()
    if any(store.is_session_turn_active(s['id']) for s in sessions) or conn.execute(
        f'SELECT 1 FROM session_turns t JOIN sessions s ON s.id=t.session_id WHERE s.user_id IN ({marks}) LIMIT 1',
        user_ids,
    ).fetchone():
        raise ValueError('Finish or stop running tasks before changing Telegram links')
    for row in conn.execute("SELECT delivery_target FROM schedule_runs WHERE status='running' AND delivery_target IS NOT NULL"):
        target = json.loads(row['delivery_target'])
        if target.get('channel') == 'telegram' and str(target.get('chat_id')) == chat_id:
            raise ValueError('Finish or stop running tasks before changing Telegram links')


async def redeem_code(chat_id: int, code: str, *, chat_type: str | None, sender_id: int | None) -> dict:
    if chat_type != 'private' or chat_id <= 0 or sender_id != chat_id:
        raise ValueError('Link Telegram from a private DM, not a group or channel')
    source_id = f'tg_{chat_id}'
    digest = _hash(code)

    def lookup(conn):
        row = conn.execute(
            'SELECT c.user_id FROM telegram_link_codes c JOIN users u ON u.id=c.user_id '
            'WHERE c.code_hash=? AND c.expires_at>? AND u.enabled=1', (digest, time.time()),
        ).fetchone()
        if not row:
            raise ValueError('Invalid or expired link code. Generate a new one in Accounts')
        existing = _linked_account(conn, str(chat_id))
        if existing:
            raise ValueError('This Telegram account is already linked. Unlink it in Accounts first')
        _require_idle(conn, [source_id, row['user_id']], str(chat_id))
        return row['user_id']

    target_id = store.with_db(lookup)
    # Wait for delayed fact extraction before snapshotting either vault.
    from app.runtime.memory.vault.extract import wait_for_extraction
    await wait_for_extraction(source_id)
    await wait_for_extraction(target_id)

    def redeem(conn):
        uid = lookup(conn)  # Revalidate after yielding (expiry, account state, replay).
        from app.runtime.memory.vault.merge import merge_accounts
        merged = merge_accounts(conn, source_id, uid)
        # The merge rebuilds its disposable index before the ownership transaction.
        try:
            conn.execute('INSERT INTO telegram_account_links VALUES (?,?,?)', (str(chat_id), uid, time.time()))
            conn.execute('DELETE FROM telegram_link_codes WHERE code_hash=?', (digest,))
            # Existing jobs keep their destination but adopt the verified owner.
            for table in ('schedules', 'schedule_runs'):
                for row in conn.execute(f'SELECT id,delivery_target FROM {table} WHERE delivery_target IS NOT NULL').fetchall():
                    target = json.loads(row['delivery_target'])
                    if (target.get('channel') == 'telegram' and str(target.get('chat_id')) == str(chat_id)
                            and target.get('user_id') in (None, source_id)):
                        target['user_id'] = uid
                        conn.execute(f'UPDATE {table} SET delivery_target=? WHERE id=?', (json.dumps(target), row['id']))
            # Adopt conversation data, never the legacy Admin's destination or
            # unrestricted mode. Current account grants govern future turns.
            conn.execute(
                "UPDATE sessions SET user_id=?, workplace_id=?, additional_workplace_ids_json='[]', "
                "execution_mode='restricted', model_profile_id=?, model_name=?, access_pending=0 "
                "WHERE user_id=?",
                (uid, personal_id, model_id, model_name, source_id),
            )
            for table in ('learning_events', 'secret_bundles'):
                conn.execute(f'UPDATE {table} SET user_id=? WHERE user_id=?', (uid, source_id))
            for row in conn.execute('SELECT id,payload_json FROM episodic_memories WHERE user_id=?', (source_id,)).fetchall():
                payload = json.loads(row['payload_json'])
                if isinstance(payload.get('scope'), dict):
                    payload['scope']['owner_id'] = uid
                for participant in payload.get('participants', []):
                    if (isinstance(participant, dict) and participant.get('type') == 'user'
                            and participant.get('id') == source_id):
                        participant['id'] = uid
                conn.execute('UPDATE episodic_memories SET user_id=?,payload_json=? WHERE id=?',
                             (uid, json.dumps(payload), row['id']))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {'user_id': uid, 'merged': merged}

    # Ownership changes use the same admission fence and confirmed teardown as
    # grant changes. Do not hold the DB lock while stopping managed work.
    with store.access._mutation_lock:
        uid = store.with_db(lookup)
        session_ids = [s['id'] for s in store.list_sessions(user_id=source_id)]
        store.access._mark_pending(session_ids)
        store.access._stop(session_ids)
        personal_id = store.access.ensure_personal_space(uid)['id']
        models = store.access.list_visible_models(uid)
        model_id = models[0]['id'] if models else ''
        model_name = models[0]['model'] if models else ''
        return store.with_db(redeem)


def unlink(user_id: str, chat_id: str) -> bool:
    def remove(conn):
        row = conn.execute('SELECT 1 FROM telegram_account_links WHERE user_id=? AND chat_id=?',
                           (user_id, chat_id)).fetchone()
        if not row:
            return False
        _require_idle(conn, [user_id], chat_id)
        conn.execute('DELETE FROM telegram_account_links WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        conn.commit()
        # Existing sessions/memories remain owned by the login account. The DM
        # starts a separate unlinked session next time, never reuses those rows.
        return True

    return store.with_db(remove)
