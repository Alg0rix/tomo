"""learning_events ledger mixin."""

from __future__ import annotations

import pytest

from app.models.mixins import learning_events as le
from app.services import store


@pytest.fixture
def db(tmp_path):
    original = getattr(store, "_path", None)
    store.rebind(tmp_path / "le.db")
    yield store
    if original is not None:
        try:
            store.rebind(original)
        except Exception:
            pass


def test_insert_and_list(db) -> None:
    e1 = db.insert_learning_event(
        agent_id="main",
        session_id="s1",
        reason="memory_every_5_turns",
        review_memory=True,
        saved=True,
        actions=["memory: added user entry"],
        diary="Noted short answers preference.",
        note="Diary: Noted short answers preference.",
        created_at=1000.0,
    )
    assert e1["id"]
    assert e1["saved"] is True
    assert e1["diary"].startswith("Noted")

    e2 = db.insert_learning_event(
        agent_id="main",
        saved=False,
        note="Nothing to save.",
        created_at=2000.0,
    )
    rows = db.list_learning_events(limit=10)
    assert len(rows) == 2
    assert rows[0]["created_at"] == 2000.0

    older = db.list_learning_events(limit=10, before=2000.0)
    assert len(older) == 1
    assert older[0]["created_at"] == 1000.0
    assert e2["id"]


def test_stats_via_mixin(db) -> None:
    db.insert_learning_event(
        saved=True,
        created_at=1,
        extract={"items": [], "memory_types": ["user"], "saved": True},
    )
    db.insert_learning_event(saved=True, created_at=2)
    db.insert_learning_event(saved=False, created_at=3)

    def _stats(conn):
        return le.learning_event_stats(conn)

    st = db.with_db(_stats)
    assert st["events_total"] == 3
    assert st["events_saved"] == 2
    assert st["events_idle"] == 1

    saved_rows = db.list_learning_events(limit=10, saved_only=True)
    assert len(saved_rows) == 2
    assert all(r["saved"] for r in saved_rows)
    typed = next(r for r in saved_rows if r["created_at"] == 1.0)
    assert "user" in typed["memory_types"]


def test_by_month(db) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ts = now.timestamp()
    db.insert_learning_event(saved=True, created_at=ts)

    def _months(conn):
        return le.learning_events_by_month(conn, months=12)

    months = db.with_db(_months)
    assert len(months) == 12
    key = f"{now.year:04d}-{now.month:02d}"
    cur = next(m for m in months if m["month"] == key)
    assert cur["events"] >= 1
    assert cur["saved"] >= 1
