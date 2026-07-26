"""Process-local busy-agent state.

Kept in memory only (a ``set`` of agent ids) — never persisted to SQLite.
Reset whenever the store rebinds to a new database (e.g. per test).
"""

from __future__ import annotations


class BusyState:
    """A simple in-memory set of busy agent ids."""

    def __init__(self) -> None:
        self._ids: set[str] = set()

    def is_busy(self, agent_id: str) -> bool:
        return agent_id in self._ids

    def set_busy(self, agent_id: str, busy: bool) -> None:
        if busy:
            self._ids.add(agent_id)
        else:
            self._ids.discard(agent_id)

    def ids(self) -> set[str]:
        return set(self._ids)
