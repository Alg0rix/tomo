"""Persisted channel jobs through real chat/store/artifact and transport seams."""

import asyncio
import sqlite3
from contextlib import asynccontextmanager

import httpx

from app.channels.delivery import register_delivery_channel
from app.channels.telegram import run_channel_turn
from app.channels.telegram_ui import TelegramTurnUI
from app.scheduler.runner import drain_pending_deliveries, fire_schedule
from app.services import store
from tests.fakes.llm import ScriptedLLM, text_reply
from tests.unit.channels.test_telegram_delivery import (
    calls,
    install,
    setup as _telegram_setup,
)

setup = _telegram_setup


def test_legacy_schedule_database_migrates_without_rerouting_jobs(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE schedules (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, agent_id TEXT NOT NULL,
                cron TEXT DEFAULT '', interval_seconds INTEGER DEFAULT 0,
                message TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
                last_run REAL, next_run REAL, created_at REAL DEFAULT 0
            );
            CREATE TABLE schedule_runs (
                id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, session_id TEXT,
                status TEXT DEFAULT 'ok', error TEXT DEFAULT '',
                started_at REAL DEFAULT 0, finished_at REAL
            );
            INSERT INTO schedules (id,name,agent_id,interval_seconds)
            VALUES ('legacy','Existing job','main',60);
            INSERT INTO schedule_runs (id,schedule_id,status)
            VALUES ('old-run','legacy','ok');
        """)
    store.rebind(path)
    assert store.get_schedule("legacy")["delivery_target"] is None
    assert store.list_schedule_runs("legacy")[0]["delivery_status"] == "local"
    assert store.pending_schedule_deliveries() == []
    store.rebind(path)  # migration is idempotent
    assert store.get_schedule("legacy")["interval_seconds"] == 60


def wire_scheduled_transport(monkeypatch, bot):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.channels.telegram.httpx.AsyncClient",
        lambda **kwargs: client(
            **{**kwargs, "transport": httpx.MockTransport(bot.transport)}
        ),
    )
    store.update_settings(
        {"telegram_enabled": True, "telegram_bot_token": "private-bot-token"}
    )


async def create_in_chat(api, monkeypatch):
    install(
        monkeypatch,
        [
            calls(
                (
                    "schedule",
                    {
                        "action": "create",
                        "schedule": "every 1h",
                        "name": "report",
                        "message": "Generate and send my report.",
                    },
                )
            ),
            text_reply("Scheduled."),
        ],
    )
    sid = store.get_or_create_session("main", "tg_-100")
    ui = TelegramTurnUI(api, -100, sid, actor_id=42, thread_id=8)
    await run_channel_turn(sid, "Schedule a report", ui=ui)
    jobs = [j for j in store.list_schedules() if j["name"] == "report"]
    assert len(jobs) == 1
    return jobs[0], sid


async def test_channel_job_survives_reload_and_delivers_file_then_final(
    setup, tmp_path, monkeypatch
):
    bot, api = setup
    wire_scheduled_transport(monkeypatch, bot)
    try:
        sch, chat_sid = await create_in_chat(api, monkeypatch)
        store.rebind(tmp_path / "delivery.db")
        sch = store.get_schedule(sch["id"])
        assert sch["delivery_target"]["chat_id"] == -100
        assert sch["delivery_target"]["thread_id"] == 8
        llm = install(
            monkeypatch,
            [
                calls(
                    (
                        "save_artifact",
                        {"filename": "report.md", "content": "# Scheduled report"},
                    )
                ),
                calls(("telegram_send_file", {"filename": "report.md"})),
                text_reply("Report delivered."),
            ],
        )
        result = await fire_schedule(sch, skip_claim=True)
        assert result["status"] == "ok"
        assert result["delivery_status"] == "sent"
        assert result["session_id"] != chat_sid
        method, parts = bot.uploads[0]
        assert method == "sendDocument"
        assert parts["chat_id"].get_payload(decode=True) == b"-100"
        assert parts["message_thread_id"].get_payload(decode=True) == b"8"
        assert parts["document"].get_payload(decode=True) == b"# Scheduled report"
        assert "reply_parameters" not in parts  # not pinned to the creation message
        final = list(bot.messages.values())[-1]
        assert final["text"] == "Report delivered."
        assert final["chat_id"] == -100 and final["message_thread_id"] == 8
        assert sum(p.get("text") == "Report delivered." for _, p in bot.calls) == 1
        runs = store.list_schedule_runs(sch["id"])
        assert runs[0]["delivery_status"] == "sent" and runs[0]["delivery_receipt"]
        assert all(
            m[0] == llm.calls[0][0][0] and t == llm.calls[0][1] for m, t in llm.calls
        )
        prefix = llm.calls[0][0][0]["content"]
        assert (
            "## Telegram conversation" in prefix
            and "## Scheduled channel delivery" in prefix
        )
        assert "private-bot-token" not in prefix and "chat_id" not in prefix
        assert "automatically delivers" in prefix
        assert not any(t["function"]["name"] == "render_ui" for t in llm.calls[0][1])

        # A different job for this same topic must not see the first job's history.
        other = store.create_schedule(
            {
                "agent_id": "main",
                "schedule": "every 1h",
                "message": "Other task",
                "delivery_target": sch["delivery_target"],
            }
        )
        install(monkeypatch, [text_reply("Other result.")])
        other_result = await fire_schedule(other, skip_claim=True)
        assert other_result["session_id"] != result["session_id"]
    finally:
        await api.aclose()


async def test_revoked_destination_or_replaced_bot_never_runs_agent(setup, monkeypatch):
    bot, api = setup
    wire_scheduled_transport(monkeypatch, bot)
    try:
        sch, _ = await create_in_chat(api, monkeypatch)
        llm = install(monkeypatch, [text_reply("Must not run.")])
        before = len(bot.calls)
        store.update_settings({"telegram_allowed_chat_ids": []})
        assert (await fire_schedule(sch, skip_claim=True))["status"] == "error"
        store.update_settings(
            {
                "telegram_allowed_chat_ids": [-100],
                "telegram_bot_token": "replacement-bot",
            }
        )
        assert (await fire_schedule(sch, skip_claim=True))["status"] == "error"
        store.update_settings(
            {"telegram_bot_token": "private-bot-token", "telegram_enabled": False}
        )
        assert (await fire_schedule(sch, skip_claim=True))["status"] == "error"
        assert not llm.calls and len(bot.calls) == before and not bot.uploads
        assert all(
            r["delivery_status"] == "not_attempted"
            for r in store.list_schedule_runs(sch["id"])
        )

        # Rotation after generation must still block BOTH files and the final send.
        store.update_settings({"telegram_enabled": True})

        class RotateDuringRun(ScriptedLLM):
            async def complete(self, messages, tools=None):
                if self.remaining == 2:
                    store.update_settings({"telegram_bot_token": "replacement-bot"})
                return await super().complete(messages, tools)

        rotating = RotateDuringRun(
            [
                calls(
                    ("save_artifact", {"filename": "report.md", "content": "private"})
                ),
                calls(("telegram_send_file", {"filename": "report.md"})),
                text_reply("Delivery unavailable."),
            ]
        )
        monkeypatch.setattr(
            "app.runtime.agent.loop.get_llm", lambda agent_id=None: rotating
        )
        outcome = await fire_schedule(sch, skip_claim=True)
        assert outcome["status"] == "ok" and outcome["delivery_status"] == "blocked"
        assert len(bot.calls) == before and not bot.uploads
        latest = store.list_schedule_runs(sch["id"])[0]
        assert latest["status"] == "ok" and latest["delivery_status"] == "blocked"
    finally:
        await api.aclose()


async def test_schedule_tool_cannot_redirect_or_manage_another_topic(
    setup, monkeypatch
):
    bot, api = setup
    wire_scheduled_transport(monkeypatch, bot)
    try:
        sch, sid = await create_in_chat(api, monkeypatch)
        llm = install(
            monkeypatch,
            [
                calls(
                    ("schedule", {"action": "list", "include_disabled": True}),
                    (
                        "schedule",
                        {
                            "action": "update",
                            "schedule_id": sch["id"],
                            "message": "stolen",
                        },
                    ),
                    (
                        "schedule",
                        {
                            "action": "create",
                            "schedule": "every 1h",
                            "message": "send elsewhere",
                            "chat_id": 42,
                        },
                    ),
                ),
                text_reply("Not permitted."),
            ],
        )
        await run_channel_turn(
            sid, "Manage schedules", ui=TelegramTurnUI(api, -100, sid, thread_id=9)
        )
        import json

        outputs = [
            json.loads(m["content"]) for m in llm.calls[-1][0] if m["role"] == "tool"
        ][-3:]
        assert outputs[0]["jobs"] == []
        assert outputs[1]["success"] is False and outputs[2]["success"] is False
        assert (
            store.get_schedule(sch["id"])["message"] == "Generate and send my report."
        )
        assert len([j for j in store.list_schedules() if j.get("delivery_target")]) == 1
    finally:
        await api.aclose()


async def test_generic_channel_recovers_saved_final_without_replay(setup, tmp_path):
    # A new channel needs only the public contract, not Telegram/scheduler/schema changes.
    received = []

    class MemoryChannel:
        def capture_current(self):
            return None

        @asynccontextmanager
        async def open(self, target, session_id):
            assert target["address"] == "inbox"
            yield self

        async def send_final(self, content, *, delivery_id):
            assert delivery_id.startswith("run_")
            received.append(content)
            if content == "uncertain":
                raise TimeoutError("remote acknowledgment lost")
            return {"id": len(received)}

    register_delivery_channel("memory-test", MemoryChannel())
    sch = store.create_schedule(
        {
            "agent_id": "main",
            "schedule": "every 1h",
            "message": "task",
            "delivery_target": {
                "version": 1,
                "channel": "memory-test",
                "address": "inbox",
            },
        }
    )

    def saved(content):
        sid = store.get_or_create_session("main", "recovery")
        rid = store.begin_schedule_run(sch["id"], session_id=sid)
        store.finish_schedule_run(rid, delivery_content=content)
        return rid

    ready = saved("saved final")
    interrupted = saved("already sending")
    assert store.claim_schedule_delivery(interrupted)
    store.rebind(tmp_path / "delivery.db")
    store.recover_schedule_deliveries()
    await asyncio.gather(drain_pending_deliveries(), drain_pending_deliveries())
    assert received == ["saved final"]
    states = {r["id"]: r for r in store.list_schedule_runs(sch["id"])}
    assert (
        states[ready]["status"] == "ok" and states[ready]["delivery_status"] == "sent"
    )
    assert states[interrupted]["delivery_status"] == "unknown"
    saved("uncertain")
    await drain_pending_deliveries()
    await drain_pending_deliveries()
    assert received == ["saved final", "uncertain"]  # no blind retry or agent rerun
    latest = store.list_schedule_runs(sch["id"])[0]
    assert latest["status"] == "ok" and latest["delivery_status"] == "unknown"
