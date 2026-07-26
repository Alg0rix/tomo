"""Process-local busy-agent and in-flight session-turn state.

Kept in memory only — never persisted to SQLite.
Reset whenever the store rebinds to a new database (e.g. per test).
"""

from __future__ import annotations


class BusyState:
    """In-memory busy agents + exclusive session turn locks."""

    def __init__(self) -> None:
        self._ids: set[str] = set()
        self._session_turns: set[str] = set()

    def is_busy(self, agent_id: str) -> bool:
        return agent_id in self._ids

    def set_busy(self, agent_id: str, busy: bool) -> None:
        if busy:
            self._ids.add(agent_id)
        else:
            self._ids.discard(agent_id)

    def ids(self) -> set[str]:
        return set(self._ids)

    def try_begin_session_turn(self, session_id: str) -> bool:
        """Acquire exclusive turn for ``session_id``. False if already running."""
        sid = (session_id or "").strip()
        if not sid:
            return False
        if sid in self._session_turns:
            return False
        self._session_turns.add(sid)
        return True

    def end_session_turn(self, session_id: str) -> None:
        sid = (session_id or "").strip()
        if sid:
            self._session_turns.discard(sid)

    def is_session_turn_active(self, session_id: str) -> bool:
        return (session_id or "").strip() in self._session_turns
