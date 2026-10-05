"""Small, user-scoped plugin services; no raw core store or recipient API."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import Context, ContextVar
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

_server_loop: asyncio.AbstractEventLoop | None = None
_in_turn_hook: ContextVar[bool] = ContextVar("plugin_turn_hook", default=False)


def bind_server_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Remember the process loop that owns agent turns. Cleared on shutdown."""
    global _server_loop
    _server_loop = loop


@contextmanager
def turn_hook():
    """Mark on_turn_end callbacks so they cannot start another agent turn."""
    token = _in_turn_hook.set(True)
    try:
        yield
    finally:
        _in_turn_hook.reset(token)


def _reject_turn_hook() -> None:
    if _in_turn_hook.get():
        raise RuntimeError("Cannot start an agent turn from on_turn_end")


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
                "CREATE TABLE IF NOT EXISTS plugin_schedules (schedule_id TEXT PRIMARY KEY, user_id TEXT, name TEXT)"
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


def _prompt(value, limit: int, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise ValueError(f"{label} must contain 1–{limit} UTF-8 bytes")
    return value.strip()


def _agent_id(uid: str, agent_id: str | None) -> str:
    from app.services import store

    if agent_id is None:
        coordinator = store.get_coordinator()
        if not coordinator:
            raise ValueError("No coordinator agent is available")
        store.access.require_use(uid, "agent", coordinator["id"])
        return coordinator["id"]
    if not isinstance(agent_id, str) or not agent_id.strip() or len(agent_id) > 64:
        raise ValueError("agent_id is invalid")
    aid = agent_id.strip()
    store.access.require_use(uid, "agent", aid)
    if not store.get_agent(aid):
        raise ValueError("Agent is unavailable")
    return aid


def _public_schedule(schedule: dict) -> dict:
    return {
        "id": schedule["id"],
        "name": schedule.get("name") or "",
        "agent_id": schedule.get("agent_id") or "",
        "schedule": schedule.get("schedule_display") or schedule.get("cron") or "",
        "enabled": bool(schedule.get("enabled")),
        "next_run": schedule.get("next_run"),
        "message": schedule.get("message") or "",
    }


def _anchor_session(api, uid: str, aid: str) -> str:
    """Stable chat whose grants a routine revalidates. Fires use a fresh session."""
    from app.services import store

    key = f"agent_session:{aid}"
    saved = api.settings.get(key, user_id=uid)
    if isinstance(saved, str) and store.get_owned_session(saved, uid):
        return saved
    sid = store.create_swarm_session([aid], uid, aid)
    api.settings.set(key, sid, user_id=uid)
    return sid


async def _launch_turn(session_id: str, prompt: str, uid: str, context) -> None:
    from app.runtime.access import bind_execution, reset_execution
    from app.services.chat import start_session_turn

    token = bind_execution(context)
    try:
        turn, queue = await start_session_turn(
            session_id, prompt, uid, origin="plugin"
        )
        turn.unsubscribe(queue)
    finally:
        reset_execution(token)


async def agent(api, prompt: str, *, user_id, agent_id) -> dict:
    """Start one tool-using turn in a new session owned by the account.

    Returns after the turn is accepted, not after the model finishes. A worker
    thread's ``asyncio.run`` hops onto the server loop when one is bound, so
    the turn is not cancelled when that temporary loop closes.
    """
    from app.services import store

    _reject_turn_hook()
    uid = active_user(user_id)
    text = _prompt(prompt, 32768, "Prompt")
    aid = _agent_id(uid, agent_id)
    sid = store.create_swarm_session([aid], uid, aid)
    try:
        context = store.access.resolve_context(uid, sid, aid)
    except Exception:
        store.delete_session(sid)
        raise

    async def launch():
        await _launch_turn(sid, text, uid, context)

    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    server = _server_loop
    try:
        if server and server.is_running() and current is not server:
            await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(launch(), server)
            )
        else:
            await launch()
    except Exception:
        from app.services.chat import get_active_session_turn

        if get_active_session_turn(sid) is None and not store.is_session_turn_active(sid):
            store.delete_session(sid)
        raise
    return {"session_id": sid, "agent_id": aid, "status": "started"}


def schedule(api, name: str, prompt: str, *, when: str, user_id, agent_id) -> dict:
    """Create a user-owned routine. It shows up in Routines and uses current grants."""
    from app.services import store

    _reject_turn_hook()
    uid = active_user(user_id)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("Schedule name must contain 1–80 characters")
    text = _prompt(prompt, 4000, "Prompt")
    if not isinstance(when, str) or not when.strip() or len(when.strip()) > 120:
        raise ValueError("Schedule must contain 1–120 characters")
    aid = _agent_id(uid, agent_id)
    label = f"{api.id}: {name.strip()}"
    if len(label) > 120:
        raise ValueError("Schedule name is too long")
    sid = _anchor_session(api, uid, aid)
    context = store.access.resolve_context(uid, sid, aid)
    created = store.access.create_schedule_for_context(
        context,
        {
            "name": label,
            "agent_id": aid,
            "schedule": when.strip(),
            "message": text,
            "enabled": True,
        },
    )
    try:
        with api.settings.database() as conn:
            conn.execute(
                "INSERT INTO plugin_schedules VALUES (?,?,?)",
                (created["id"], uid, label),
            )
            conn.commit()
    except Exception:
        store.delete_schedule(created["id"])
        raise
    return _public_schedule(created)


def unschedule(api, schedule_id: str, *, user_id) -> None:
    from app.services import store

    _reject_turn_hook()
    uid = active_user(user_id)
    if not isinstance(schedule_id, str) or not schedule_id.strip():
        raise ValueError("schedule_id is required")
    with api.settings.database() as conn:
        row = conn.execute(
            "SELECT 1 FROM plugin_schedules WHERE schedule_id=? AND user_id=?",
            (schedule_id, uid),
        ).fetchone()
        if not row:
            raise PermissionError("Schedule is unavailable")
    store.delete_schedule(schedule_id)
    with api.settings.database() as conn:
        conn.execute(
            "DELETE FROM plugin_schedules WHERE schedule_id=? AND user_id=?",
            (schedule_id, uid),
        )
        conn.commit()


def schedules(api, *, user_id) -> list[dict]:
    from app.services import store

    uid = active_user(user_id)
    with api.settings.database() as conn:
        rows = conn.execute(
            "SELECT schedule_id FROM plugin_schedules WHERE user_id=? ORDER BY name",
            (uid,),
        ).fetchall()
    visible = []
    stale = []
    for (schedule_id,) in rows:
        schedule = store.get_schedule(schedule_id)
        if not schedule or schedule.get("owner_user_id") != uid:
            stale.append(schedule_id)
            continue
        visible.append(_public_schedule(schedule))
    if stale:
        with api.settings.database() as conn:
            conn.executemany(
                "DELETE FROM plugin_schedules WHERE schedule_id=? AND user_id=?",
                [(schedule_id, uid) for schedule_id in stale],
            )
            conn.commit()
    return visible
