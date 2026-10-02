"""Persistent process cards and origin-bound continuations outside a turn UI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
import weakref
from typing import Any

from app.channels.delivery import DeliveryBlocked
from app.channels.telegram_delivery import ScheduledTelegramAPI, _check_target
from app.services.store import store

logger = logging.getLogger(__name__)
_dispatcher = None
_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
ACTIVE = {"starting", "running", "stopping", "unknown"}


def bind_dispatcher(dispatcher) -> None:
    global _dispatcher
    _dispatcher = dispatcher
    from app.services.background_continuation import wake_pending

    wake_pending()


def unbind_dispatcher(dispatcher) -> None:
    global _dispatcher
    if _dispatcher is dispatcher:
        _dispatcher = None


def _target(job: dict[str, Any]) -> dict[str, Any]:
    target = job.get("delivery") or {}
    session = store.get_session(job["session_id"])
    if (target.get("channel") != "telegram" or not session
            or session["user_id"] != job["user_id"]
            or session.get("telegram_chat_id") != str(target.get("chat_id"))):
        raise DeliveryBlocked("Job does not belong to this Telegram conversation")
    _check_target(target)
    return target


class JobTelegramAPI(ScheduledTelegramAPI):
    """Revalidate the originating records on every HTTP attempt and retry."""

    def __init__(self, target: dict, ids: list[str]):
        self.job_ids = ids
        super().__init__(target)

    def _check_access(self):
        super()._check_access()
        for jid in self.job_ids:
            job = store.get_background_job(jid)
            if not job or job.get('continuation_suppressed') or _target(job) != self.target:
                raise DeliveryBlocked('The originating job conversation is no longer authorized')


def _text(job: dict[str, Any]) -> str:
    label = job["id"][-8:]
    rc = f" · exit {job['returncode']}" if job.get("returncode") is not None else ""
    lines = [f"⚙ {label} · {str(job['command'])[:1000]}",
             f"{job['status'].title()}{rc} · {job['backend']}"]
    if job.get("monitoring_closed"):
        lines.append("Monitoring closed; the remote process may still be running.")
    elif store.background_jobs_paused(job["session_id"]):
        lines.append("Agent continuation paused. Send a message in the original conversation to continue.")
    elif job.get("continuation_status") in {"pending", "claimed"}:
        lines.append("Result waiting for the agent." if job["continuation_status"] == "pending" else "Agent is reading the result.")
    elif job["status"] in ACTIVE:
        lines.append("The process continues after the agent's turn ends.")
    if job.get("delivery_status") in {"blocked", "unknown"}:
        lines.append("Final delivery: " + job["delivery_status"])
    return "\n".join(lines)


def _buttons(job: dict[str, Any]) -> dict:
    jid = job["id"]
    rows = [[{"text": "Refresh status", "callback_data": f"tj:{jid}:status"},
             {"text": "Latest log", "callback_data": f"tj:{jid}:log"}]]
    if job["status"] in ACTIVE and not job.get("monitoring_closed"):
        rows.append([{"text": "Stop process", "callback_data": f"tj:{jid}:stop"}])
        if job["status"] == "unknown":
            rows[-1].append({"text": "Close monitoring", "callback_data": f"tj:{jid}:close"})
    return {"inline_keyboard": rows}


async def update_card(job_id: str, *, force: bool = False) -> None:
    lock = _locks.setdefault(job_id, asyncio.Lock())
    async with lock:
        job = store.get_background_job(job_id)
        if not job or not job.get("delivery"):
            return
        try:
            target = _target(job)
        except DeliveryBlocked:
            return
        text = _text(job)
        if not force and job.get("card_text") == text:
            return
        if job.get("card_confirmation") and not force:
            confirm = job["card_confirmation"]
            if confirm.get("expires", 0) > time.time():
                return
        api = JobTelegramAPI(target, [job_id])
        try:
            if job.get("card_message_id"):
                await api.edit_message(target["chat_id"], job["card_message_id"], text,
                                       reply_markup=_buttons(job))
            else:
                # An ambiguous send must not create a second card on the next event.
                if job.get("card_delivery_status") in {"sending", "unknown"}:
                    return
                store.update_background_job(job_id, {"card_delivery_status": "sending"})
                receipt = await api.send_message(target["chat_id"], text,
                                                 thread_id=target.get("thread_id"),
                                                 reply_to=target.get("reply_to"), silent=True,
                                                 reply_markup=_buttons(job))
                if not receipt.get("message_id"):
                    raise RuntimeError("Telegram did not confirm the process card")
                store.update_background_job(job_id, {"card_message_id": receipt["message_id"],
                                                     "card_delivery_status": "sent"})
            store.update_background_job(job_id, {"card_text": text, "card_confirmation": None})
        except DeliveryBlocked:
            store.update_background_job(job_id, {"card_delivery_status": "blocked"})
        except Exception:
            store.update_background_job(job_id, {"card_delivery_status": "unknown"})
            logger.warning("Could not deliver process card job=%s", job_id)
        finally:
            await api.aclose()


def session_for_reply(message: dict[str, Any]) -> str | None:
    """An explicit reply to a persistent card can address its old conversation."""
    reply_id = (message.get("reply_to_message") or {}).get("message_id")
    if not reply_id:
        return None
    for job in store.list_background_jobs(card_message_id=reply_id, include_logs=False):
        try:
            target = _target(job)
        except DeliveryBlocked:
            continue
        if ((message.get("chat") or {}).get("id") == target["chat_id"]
                and message.get("message_thread_id") == target.get("thread_id")
                and (message.get("from") or {}).get("id") == target.get("actor_id")):
            return job["session_id"]
    return None


async def callback(query: dict[str, Any], api) -> bool:
    data = str(query.get("data") or "")
    if not data.startswith("tj:"):
        return False
    parts = data.split(":")
    callback_id = str(query.get("id") or "")
    job = store.get_background_job(parts[1]) if len(parts) in {3, 4} else None
    message = query.get("message") or {}
    try:
        if not job:
            raise DeliveryBlocked("This process control is unavailable.")
        target = _target(job)
        if (message.get("message_id") != job.get("card_message_id")
                or (message.get("chat") or {}).get("id") != target["chat_id"]
                or message.get("message_thread_id") != target.get("thread_id")
                or (query.get("from") or {}).get("id") != target.get("actor_id")
                or message.get("forward_origin") or message.get("forward_date")):
            raise DeliveryBlocked("Only the originating person and topic can control this job.")
        action = parts[2]
        bound_api = JobTelegramAPI(target, [job['id']])
        try:
            await bound_api.answer_callback(callback_id, "Received")
            if action == "status":
                await update_card(job["id"], force=True)
            elif action == "log":
                from app.services.background_jobs import manager

                log = manager.logs(job["session_id"], job["id"], tail=1024 * 1024)
                text = str(log.get("text") or (str(log.get("stdout") or "") + "\n" + str(log.get("stderr") or ""))).strip()
                note = "Retained log (older output truncated)." if log.get("truncated") else "Retained log."
                if log.get("logs_expired"):
                    text = "The retained log expired."
                if len(text) > 3000:
                    from app.runtime.artifacts.fs import ensure_artifacts_dir

                    filename = f"{job['id']}.log"
                    path = ensure_artifacts_dir(job["session_id"]) / filename
                    await asyncio.to_thread(path.write_text, text, encoding="utf-8")
                    _target(job)
                    await bound_api.send_file(target["chat_id"], filename, text.encode(),
                                              caption=note, kind="document", silent=True,
                                              thread_id=target.get("thread_id"))
                else:
                    await bound_api.send_message(target["chat_id"], text or "No output yet.",
                                                 silent=True, thread_id=target.get("thread_id"))
            elif action in {"stop", "close"}:
                nonce = secrets.token_hex(4)
                store.update_background_job(job["id"], {"card_confirmation": {
                    "action": action, "nonce": nonce, "expires": time.time() + 120}})
                text = ("Stop this process and its children?" if action == "stop" else
                        "Close monitoring? The process may still run. This does not stop it.")
                await bound_api.edit_message(target["chat_id"], job["card_message_id"],
                                             text + "\n" + job["command"][:1000], reply_markup={"inline_keyboard": [[
                                                 {"text": "Confirm", "callback_data": f"tj:{job['id']}:confirm:{nonce}"},
                                                 {"text": "Cancel", "callback_data": f"tj:{job['id']}:status"}]]})
            elif action == "confirm" and len(parts) == 4:
                from app.services.background_jobs import manager

                confirmation = job.get("card_confirmation") or {}
                if confirmation.get("nonce") != parts[3] or confirmation.get("expires", 0) < time.time():
                    return True
                store.update_background_job(job["id"], {"card_confirmation": None})
                operation = manager.stop_job if confirmation["action"] == "stop" else manager.close_monitoring
                await asyncio.to_thread(operation, job["session_id"], job["id"])
                await update_card(job["id"], force=True)
        finally:
            await bound_api.aclose()
    except DeliveryBlocked as exc:
        await api.answer_callback(callback_id, str(exc), alert=True)
    except Exception:
        logger.warning("Process control failed job=%s", job.get("id") if job else "unavailable")
        with contextlib.suppress(Exception):
            await api.answer_callback(callback_id, "Could not update this process. Refresh its status.", alert=True)
    return True


def admit_continuation(jobs: list[dict[str, Any]]) -> bool:
    dispatcher = _dispatcher
    if dispatcher is None or dispatcher.closing:
        return False
    target = _target(jobs[0])
    chat_id = target["chat_id"]
    task = dispatcher.tasks.get(chat_id)
    if ((task and not task.done()) or dispatcher.pending.get(chat_id)
            or len(dispatcher.tasks) >= dispatcher.MAX_ACTIVE_CHATS):
        return False
    # This is an internal completion item, never synthesized inbound user text.
    item = {"text": "", "message": {
        "from": {"id": target.get("actor_id")},
        "message_thread_id": target.get("thread_id"),
        "message_id": target.get("reply_to"), "_tomo_background_jobs": [j["id"] for j in jobs],
        "_tomo_job_session": jobs[0]["session_id"],
    }}
    dispatcher.actors[chat_id] = (target.get("actor_id"), target.get("thread_id"))
    dispatcher.tasks[chat_id] = asyncio.create_task(dispatcher._drive(chat_id, item))
    dispatcher.tasks[chat_id]._tomo_session_id = jobs[0]['session_id']
    return True


async def run_continuation(dispatcher, message: dict[str, Any]) -> dict | None:
    from app.channels.telegram import run_channel_turn
    from app.channels.telegram_ui import TelegramTurnUI
    from app.services.chat import SessionTurnBusy
    from app.services.turn_recovery import acknowledge_delivery

    ids = message["_tomo_background_jobs"]
    jobs = [store.get_background_job(jid) for jid in ids]
    if any(not job or job.get("continuation_status") != "pending" for job in jobs):
        return None
    try:
        target = _target(jobs[0])
    except DeliveryBlocked as exc:
        for job in jobs:
            store.update_background_job(job['id'], {'continuation_status': 'blocked',
                                                    'delivery_status': 'blocked', 'error': str(exc)})
        return None
    api = JobTelegramAPI(target, ids)
    sid = jobs[0]["session_id"]
    ui = TelegramTurnUI(api, target["chat_id"], sid, actor_id=target.get("actor_id"),
                        thread_id=target.get("thread_id"), reply_to=jobs[0].get("card_message_id"))
    dispatcher.uis[target["chat_id"]] = ui
    ui.input_mode = dispatcher.modes.get(target["chat_id"], "steer")
    ui.on_stop = lambda: dispatcher.stopped.add(target["chat_id"])
    ui.on_mode = lambda mode: dispatcher.modes.__setitem__(target["chat_id"], mode)
    try:
        await ui.start()
        if ui.stop_requested:
            return None
        _target(jobs[0])
        reply = await run_channel_turn(sid, "", ui=ui, background_job_ids=ids)
        _target(jobs[0])
        for jid in ids:
            store.update_background_job(jid, {"result_text": reply, "delivery_status": "sending"})
        # Capture a confirmed final receipt without changing normal turn UI behavior.
        await ui.finish("Background job result · " + ", ".join(jid[-8:] for jid in ids) + "\n\n" + reply)
        if not isinstance(ui.final_receipt, dict) or not ui.final_receipt.get('message_id'):
            raise RuntimeError('Telegram did not confirm final delivery')
        for jid in ids:
            store.update_background_job(jid, {"delivery_status": "sent", "delivery_receipt": ui.final_receipt})
        acknowledge_delivery(sid)
        return {"session_id": sid, "reply": reply, "outcome": "Done"}
    except SessionTurnBusy:
        return None  # Existing turn cleanup will signal admission again.
    except DeliveryBlocked:
        for jid in ids:
            job = store.get_background_job(jid) or {}
            store.update_background_job(jid, {"delivery_status": "unknown" if job.get("delivery_status") == "sending" else "blocked",
                                              "continuation_status": 'consumed' if job.get('continuation_status') == 'consumed' else "blocked"})
    except asyncio.CancelledError:
        for jid in ids:
            job = store.get_background_job(jid) or {}
            updates = {"continuation_status": 'consumed' if job.get('continuation_status') == 'consumed' else "cancelled"}
            if job.get("delivery_status") == "sending":
                updates["delivery_status"] = "unknown"
            store.update_background_job(jid, updates)
        raise
    except Exception:
        for jid in ids:
            job = store.get_background_job(jid) or {}
            store.update_background_job(jid, {"delivery_status": "unknown", "continuation_status":
                                              'consumed' if job.get('continuation_status') == 'consumed' else "cancelled"})
        logger.exception("Telegram background result failed session=%s", sid)
    finally:
        await ui.close()
        dispatcher.uis.pop(target["chat_id"], None)
        await api.aclose()
        for jid in ids:
            await update_card(jid)
    return None


async def deliver_stored_final(ids: list[str]) -> None:
    """Restart outbox: claim saved text once; uncertain sends stay unknown."""
    def claim(conn):
        from app.models.mixins.background_jobs import get_background_job, _write

        with conn:
            conn.execute('BEGIN IMMEDIATE')
            jobs = [get_background_job(conn, jid) for jid in ids]
            if any(not job or job['continuation_status'] != 'consumed'
                   or job['delivery_status'] != 'pending' for job in jobs):
                return None
            if any(job['delivery'] != jobs[0]['delivery'] or job['result_text'] != jobs[0]['result_text']
                   for job in jobs):
                return None
            for job in jobs:
                job.update(delivery_status='sending', version=job['version'] + 1)
                _write(conn, job)
            return jobs

    jobs = store.with_db(claim)
    if not jobs:
        return
    api = None
    attempted = False
    try:
        target = _target(jobs[0])
        api = JobTelegramAPI(target, ids)
        text = 'Background job result · ' + ', '.join(jid[-8:] for jid in ids) + '\n\n' + jobs[0]['result_text']
        attempted = True
        receipt = await api.send_answer(target['chat_id'], text, thread_id=target.get('thread_id'),
                                        reply_to=jobs[0].get('card_message_id') or target.get('reply_to'))
        if not isinstance(receipt, dict) or not receipt.get('message_id'):
            raise RuntimeError('Telegram did not confirm final delivery')
        for jid in ids:
            store.update_background_job(jid, {'delivery_status': 'sent', 'delivery_receipt': receipt})
        from app.services.turn_recovery import acknowledge_delivery

        acknowledge_delivery(jobs[0]['session_id'])
    except DeliveryBlocked:
        for jid in ids:
            store.update_background_job(jid, {'delivery_status': 'unknown' if attempted else 'blocked'})
    except asyncio.CancelledError:
        for jid in ids:
            store.update_background_job(jid, {'delivery_status': 'unknown' if attempted else 'pending'})
        raise
    except Exception:
        for jid in ids:
            store.update_background_job(jid, {'delivery_status': 'unknown'})
        logger.warning('Stored background final could not be delivered job=%s', ids[0])
    finally:
        if api:
            await api.aclose()
        for jid in ids:
            await update_card(jid)
