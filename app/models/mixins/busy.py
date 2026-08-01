"""Process-local busy-agent and in-flight session-turn state.

Busy is **per session**: an agent working in session A must not appear busy
in session B (or in global agent lists / the rail).

Kept in memory only — never persisted to SQLite.
Reset whenever the store rebinds to a new database (e.g. per test).
"""

from __future__ import annotations


class BusyState:
    """In-memory per-session agent busy flags + exclusive session turn locks."""

    def __init__(self) -> None:
        # agent_id → session_ids where that agent is currently running a turn
        self._by_agent: dict[str, set[str]] = {}
        self._session_turns: set[str] = set()

    def is_busy(self, agent_id: str, session_id: str | None = None) -> bool:
        """True only when ``session_id`` is set and the agent is busy there.

        Without a session id, always False — busy is never a global UI signal.
        """
        aid = (agent_id or "").strip()
        if not aid:
            return False
        sessions = self._by_agent.get(aid)
        if not sessions:
            return False
        sid = (session_id or "").strip()
        if not sid:
            return False
        return sid in sessions

    def set_busy(self, agent_id: str, busy: bool, *, session_id: str) -> None:
        aid = (agent_id or "").strip()
        sid = (session_id or "").strip()
        if not aid or not sid:
            return
        if busy:
            self._by_agent.setdefault(aid, set()).add(sid)
            return
        current = self._by_agent.get(aid)
        if not current:
            return
        current.discard(sid)
        if not current:
            self._by_agent.pop(aid, None)

    def clear_agent(self, agent_id: str) -> None:
        """Drop busy for an agent in every session (e.g. agent deleted)."""
        aid = (agent_id or "").strip()
        if aid:
            self._by_agent.pop(aid, None)

    def ids(self) -> set[str]:
        """Agents busy in *any* session — not for UI; empty for agent list busy."""
        return set()

    def ids_for_session(self, session_id: str) -> set[str]:
        sid = (session_id or "").strip()
        if not sid:
            return set()
        return {aid for aid, sids in self._by_agent.items() if sid in sids}

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
