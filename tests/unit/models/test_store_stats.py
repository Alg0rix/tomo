"""Stats / dashboard snapshot tests for the SQLite-backed store.

Covers:
* P2 — ``dashboard_data()["recent_agents"]`` sorted by ``created_at`` DESC.
* P2 — ``stats`` / ``dashboard_data`` read agents + sessions under a single
  lock acquisition (atomic, no torn snapshot).
"""

from __future__ import annotations

import threading

from app.services import store


def _rebind(tmp_path) -> None:
    store.rebind(tmp_path / "stats.db")


def test_dashboard_recent_agents_newest_first(tmp_path) -> None:
    _rebind(tmp_path)
    # Create a fresh agent after seeding -> it must be the newest (first).
    store.create_agent({"id": "fresh", "name": "Fresh"})
    recent = store.dashboard_data()["recent_agents"]
    times = [a["created_at"] for a in recent]
    assert times == sorted(times, reverse=True)  # strictly descending
    assert recent[0]["id"] == "fresh"
    assert len(recent) == 5  # 4 seeded + 1 fresh, capped at 5


def test_stats_counts_match_lists(tmp_path) -> None:
    _rebind(tmp_path)
    stats = store.stats()
    assert stats["agent_count"] == len(store.list_agents())
    assert stats["session_count"] == len(store.list_sessions())
    enabled = [a for a in store.list_agents() if a["enabled"]]
    assert stats["enabled_agent_count"] == len(enabled)


def test_dashboard_stats_equal_to_stats(tmp_path) -> None:
    """``_stats_from`` helper must produce the same shape as ``stats()``."""
    _rebind(tmp_path)
    store.create_agent({"id": "x", "name": "X"})
    # No concurrent mutation between calls -> the two snapshots are equal and
    # prove the dashboard reuses the same computation as ``stats()``.
    assert store.dashboard_data()["stats"] == store.stats()


def test_stats_dashboard_atomic_under_concurrency(tmp_path) -> None:
    """Concurrent readers + a writer must not error or tear snapshots."""
    _rebind(tmp_path)
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(200):
                s = store.stats()
                d = store.dashboard_data()
                # agent_count is stable (writer only adds sessions), so the
                # dashboard's own stats must agree with a standalone stats call
                # seen in the same reader iteration.
                assert d["stats"]["agent_count"] == s["agent_count"] == 4
                assert s["session_count"] >= 3
                # recent_agents always sorted newest-first, capped at 5.
                times = [a["created_at"] for a in d["recent_agents"]]
                assert times == sorted(times, reverse=True)
        except BaseException as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    def writer() -> None:
        try:
            for _ in range(50):
                store.create_swarm_session(["main"])
        except BaseException as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads += [threading.Thread(target=writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
