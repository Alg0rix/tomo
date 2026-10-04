"""Small, user-scoped plugin services; no raw core store or recipient API."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import Context
import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def active_user(user_id: str | None = None) -> str:
    from app.runtime.tools.user_ctx import current_user_id
    from app.services import store

    uid = user_id if user_id is not None else current_user_id()
    user = store.get_user(uid)
    if not user or not user.get("enabled"):
        raise PermissionError("Plugin service requires an active account")
    return uid


class PluginSettings:
    """Atomic JSON values scoped by plugin and authenticated account."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "sdk.sqlite3"

    @contextmanager
    def database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS settings (user_id TEXT, key TEXT, value TEXT, PRIMARY KEY(user_id, key))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS targets (id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, target TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS generation_budget (user_id TEXT, hour INTEGER, calls INTEGER, PRIMARY KEY(user_id, hour))"
            )
            yield conn
        finally:
            conn.close()

    @staticmethod
    def key(key: str) -> str:
        if not isinstance(key, str) or not key or len(key) > 128:
            raise ValueError("Settings key must contain 1–128 characters")
        return key

    def get(self, key: str, default=None, *, user_id: str | None = None):
        uid = active_user(user_id)
        key = self.key(key)
        with self.database() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE user_id=? AND key=?", (uid, key)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value, *, user_id: str | None = None) -> None:
        uid = active_user(user_id)
        key = self.key(key)
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError("Settings values may not exceed 64 KiB")
        with self.database() as conn:
            conn.execute(
                "INSERT INTO settings VALUES (?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
                (uid, key, encoded),
            )
            conn.commit()

    def delete(self, key: str, *, user_id: str | None = None) -> None:
        uid = active_user(user_id)
        key = self.key(key)
        with self.database() as conn:
            conn.execute("DELETE FROM settings WHERE user_id=? AND key=?", (uid, key))
            conn.commit()


class BackgroundTask:
    """One serial periodic worker, with cooperative cancellation."""

    def __init__(self, plugin_id, callback, interval):
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=Context().run,
            args=(self._run,),
            name=f"plugin-{plugin_id}",
            daemon=True,
        )
        self.callback = callback
        self.interval = interval

    def _run(self):
        while not self.stop.is_set():
            try:
                self.callback(self.stop)
            except Exception:
                logger.exception("Plugin background task failed: %s", self.thread.name)
            if self.stop.wait(self.interval):
                break

    def start(self):
        self.thread.start()

    def cancel(self):
        self.stop.set()

    def join(self, timeout=5):
        if (
            self.thread.ident is not None
            and self.thread is not threading.current_thread()
        ):
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                logger.warning(
                    "Plugin worker did not stop within 5s: %s", self.thread.name
                )


def finite_timeout(timeout, maximum=60):
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or not 0 < timeout <= maximum
    ):
        raise ValueError(f"Timeout must be finite and between 0 and {maximum} seconds")


def capture_notification(api, user_id):
    from app.channels.delivery import capture_current_target
    from app.runtime.artifacts.fs import current_session_id
    from app.services import store

    uid = active_user(user_id)
    sid = current_session_id()
    if not sid or not store.session_owned_by(sid, uid):
        raise PermissionError("Notification capture requires an owned active session")
    target = capture_current_target()
    if target is None:
        raise ValueError("This channel does not support notifications")
    if target.get("user_id") != uid:
        raise PermissionError("Notification target belongs to another account")
    token = uuid.uuid4().hex
    with api.settings.database() as conn:
        conn.execute(
            "INSERT INTO targets VALUES (?,?,?,?)",
            (token, uid, sid, json.dumps(target)),
        )
        conn.commit()
    return token


async def notify(api, target_id, message, user_id):
    from app.channels.delivery import open_delivery
    from app.services import store

    uid = active_user(user_id)
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        raise ValueError("Notification message must contain 1–4000 characters")
    with api.settings.database() as conn:
        row = conn.execute(
            "SELECT session_id,target FROM targets WHERE id=? AND user_id=?",
            (target_id, uid),
        ).fetchone()
    if not row or not store.session_owned_by(row[0], uid):
        raise PermissionError("Notification target is unavailable")
    async with open_delivery(json.loads(row[1]), row[0]) as delivery:
        return await delivery.send_final(
            message, delivery_id=f"plugin:{api.id}:{uuid.uuid4().hex}"
        )


async def generate(api, prompt, *, profile_id, max_output_tokens, timeout, user_id):
    from app.runtime.llm import get_llm
    from app.services import store

    uid = active_user(user_id)
    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt.encode()) > 32768
    ):
        raise ValueError("Prompt must contain 1–32768 UTF-8 bytes")
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4096:
        raise ValueError("max_output_tokens must be between 1 and 4096")
    finite_timeout(timeout)
    client = get_llm(profile_id=profile_id, max_output_tokens=max_output_tokens)
    try:
        # Reserve before making a paid call; failures also consume a slot.
        with api.settings.database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            hour = int(time.time() // 3600)
            conn.execute("DELETE FROM generation_budget WHERE hour < ?", (hour,))
            row = conn.execute(
                "SELECT calls FROM generation_budget WHERE user_id=? AND hour=?",
                (uid, hour),
            ).fetchone()
            if row and row[0] >= 30:
                raise ValueError(
                    "Plugin generation limit reached (30 calls/account/hour)"
                )
            conn.execute(
                "INSERT INTO generation_budget VALUES (?,?,1) ON CONFLICT(user_id,hour) DO UPDATE SET calls=calls+1",
                (uid, hour),
            )
            conn.commit()
        response = await asyncio.wait_for(
            client.complete([{"role": "user", "content": prompt}]), timeout
        )
        # Share the existing usage ledger without emitting a fake agent turn.
        sid = f"plugin:{api.id}:{hashlib.sha256(uid.encode()).hexdigest()}"

        def record(conn):
            conn.execute(
                "INSERT INTO usage_events (session_id,agent_id,created_at,turns,prompt_tokens,completion_tokens,message_preview) VALUES (?,?,?,0,?,?,?)",
                (
                    sid,
                    f"plugin:{api.id}",
                    time.time(),
                    response.prompt_tokens,
                    response.completion_tokens,
                    "Plugin generation",
                ),
            )
            conn.commit()

        store.with_db(record)
        return {
            "content": response.content or "",
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }
    finally:
        await client.aclose()
