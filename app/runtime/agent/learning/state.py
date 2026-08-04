"""Per-agent learning counters with sticky dues and cumulative skill iters.

Invariants
----------
* Counters advance on every successful top-level final.
* Triggers are sticky until a review **starts** (cooldown / in-flight skips
  must not burn the nudge).
* Skill iters are **cumulative across turns**, not only tools on this turn.
* Skill-touched this turn always arms skill review (refine-in-place).
* Nested / review-fork / non-final turns never advance or fire.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_MEMORY_NUDGE = 5
_DEFAULT_SKILL_NUDGE = 5
_DEFAULT_COOLDOWN_SEC = 90.0
_MAX_AGENT_STATES = 256
_MAX_HYDRATED_SESSIONS = 512

_lock = threading.RLock()
_agents: dict[str, "AgentLearningState"] = {}
_hydrated_sessions: set[str] = set()
# Task-local: only the review asyncio task sees True (other turns keep counting).
_in_review: ContextVar[bool] = ContextVar("tomo_learning_in_review", default=False)


def _settings_int(key: str, default: int) -> int:
    try:
        from app.services import store

        raw = store.get_settings().get(key, default)
        return max(0, int(raw))  # 0 = disabled for that nudge
    except (TypeError, ValueError, Exception):
        return default


def _settings_float(key: str, default: float) -> float:
    try:
        from app.services import store

        raw = store.get_settings().get(key, default)
        return max(0.0, float(raw))
    except (TypeError, ValueError, Exception):
        return default


def learning_enabled() -> bool:
    try:
        from app.services import store

        return bool(store.get_settings().get("learning_enabled", True))
    except Exception:
        return True


def memory_nudge_turns() -> int:
    return _settings_int("learning_memory_nudge_turns", _DEFAULT_MEMORY_NUDGE)


def skill_nudge_iters() -> int:
    return _settings_int("learning_skill_nudge_iters", _DEFAULT_SKILL_NUDGE)


def cooldown_seconds() -> float:
    return _settings_float("learning_cooldown_sec", _DEFAULT_COOLDOWN_SEC)


@dataclass
class ReviewPlan:
    """What the next background review should focus on."""

    agent_id: str
    review_memory: bool = False
    review_skills: bool = False
    skills_touched: list[str] = field(default_factory=list)
    tool_calls_this_turn: int = 0
    turns_since_memory: int = 0
    iters_since_skill: int = 0
    reason: str = ""

    @property
    def any(self) -> bool:
        return self.review_memory or self.review_skills

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "review_memory": self.review_memory,
            "review_skills": self.review_skills,
            "skills_touched": list(self.skills_touched),
            "tool_calls_this_turn": self.tool_calls_this_turn,
            "turns_since_memory": self.turns_since_memory,
            "iters_since_skill": self.iters_since_skill,
            "reason": self.reason,
        }


@dataclass
class AgentLearningState:
    turns_since_memory: int = 0
    iters_since_skill: int = 0
    skill_refine_pending: bool = False
    memory_due: bool = False  # sticky until review starts
    skills_due: bool = False
    last_review_at: float = 0.0
    in_flight: bool = False
    last_skills_touched: list[str] = field(default_factory=list)
    reviews_started: int = 0
    reviews_saved: int = 0
    reviews_skipped_cooldown: int = 0
    reviews_skipped_inflight: int = 0


def _key(agent_id: str | None) -> str:
    return (agent_id or "").strip() or "_default"


def get_state(agent_id: str | None) -> AgentLearningState:
    with _lock:
        k = _key(agent_id)
        st = _agents.get(k)
        if st is None:
            if len(_agents) >= _MAX_AGENT_STATES:
                # Drop oldest insertion (dict preserves order on 3.12+).
                drop = next(iter(_agents))
                del _agents[drop]
            st = AgentLearningState()
            _agents[k] = st
        return st


def enter_review_scope() -> object:
    """Mark *this* task as a learning-review fork (no recursive observe).

    Uses a ContextVar so concurrent agent turns keep advancing counters.
    Returns a token for :func:`exit_review_scope`.
    """
    return _in_review.set(True)


def exit_review_scope(token: object | None = None) -> None:
    if token is not None:
        try:
            _in_review.reset(token)  # type: ignore[arg-type]
            return
        except (ValueError, TypeError):
            pass
    _in_review.set(False)


def in_review_scope() -> bool:
    return bool(_in_review.get())


def hydrate_from_session(session_id: str | None, agent_id: str | None) -> None:
    """Seed turn counter from prior user messages after process restart.

    Only runs once per process+session. Does not invent skill iters.
    """
    sid = (session_id or "").strip()
    if not sid or not learning_enabled():
        return
    with _lock:
        if sid in _hydrated_sessions:
            return
        if len(_hydrated_sessions) >= _MAX_HYDRATED_SESSIONS:
            _hydrated_sessions.clear()
        _hydrated_sessions.add(sid)

    mem_n = memory_nudge_turns()
    if mem_n <= 0:
        return
    try:
        from app.services import store

        history = store.get_session_history(sid) or []
    except Exception:
        return
    user_turns = sum(
        1
        for e in history
        if isinstance(e, dict)
        and (
            e.get("type") in ("user", "human")
            or e.get("role") == "user"
        )
    )
    if user_turns <= 0:
        return
    with _lock:
        st = get_state(agent_id)
        # Only seed if still at zero (fresh process / new agent bind).
        if st.turns_since_memory == 0 and not st.memory_due:
            st.turns_since_memory = user_turns % mem_n


def observe_turn(
    *,
    agent_id: str | None,
    tool_calls: int = 0,
    skills_touched: list[str] | None = None,
    nested: bool = False,
    ended_kind: str | None = "final",
) -> ReviewPlan | None:
    """Advance counters after a successful top-level final; return a plan or None.

    Sticky dues survive cooldown/in-flight skips so the next eligible turn
    still reviews. Counters for fired axes reset only in :func:`begin_review`.
    """
    if nested or in_review_scope() or not learning_enabled():
        return None
    if ended_kind != "final":
        return None

    skills = [s for s in (skills_touched or []) if isinstance(s, str) and s.strip()]
    skills = [s.strip() for s in skills]
    n_tools = max(0, int(tool_calls or 0))
    mem_n = memory_nudge_turns()
    skill_n = skill_nudge_iters()
    aid = _key(agent_id)

    with _lock:
        st = get_state(aid)
        st.last_skills_touched = list(skills)

        # Memory: count successful top-level turns
        if mem_n > 0:
            st.turns_since_memory += 1
            if st.turns_since_memory >= mem_n:
                st.memory_due = True

        # Skills: cumulative tool iters + refine-on-touch
        if skill_n > 0:
            st.iters_since_skill += n_tools
            if skills:
                st.skill_refine_pending = True
            if st.iters_since_skill >= skill_n or st.skill_refine_pending:
                st.skills_due = True

        if not st.memory_due and not st.skills_due:
            return None

        if st.in_flight:
            st.reviews_skipped_inflight += 1
            return None

        cool = cooldown_seconds()
        if cool > 0 and st.last_review_at > 0:
            if (time.monotonic() - st.last_review_at) < cool:
                st.reviews_skipped_cooldown += 1
                return None

        reasons: list[str] = []
        if st.memory_due:
            reasons.append(f"memory_every_{mem_n}_turns")
        if st.skills_due:
            if st.skill_refine_pending and skills:
                reasons.append("skill_touched")
            elif st.iters_since_skill >= skill_n > 0:
                reasons.append(f"skill_iters>={skill_n}")
            else:
                reasons.append("skill_due")

        return ReviewPlan(
            agent_id=aid,
            review_memory=st.memory_due,
            review_skills=st.skills_due,
            skills_touched=list(skills),
            tool_calls_this_turn=n_tools,
            turns_since_memory=st.turns_since_memory,
            iters_since_skill=st.iters_since_skill,
            reason="+".join(reasons),
        )


def begin_review(plan: ReviewPlan) -> bool:
    """Claim in-flight + reset sticky dues for axes this plan covers."""
    with _lock:
        st = get_state(plan.agent_id)
        if st.in_flight:
            st.reviews_skipped_inflight += 1
            return False
        cool = cooldown_seconds()
        if cool > 0 and st.last_review_at > 0:
            if (time.monotonic() - st.last_review_at) < cool:
                st.reviews_skipped_cooldown += 1
                return False
        st.in_flight = True
        st.last_review_at = time.monotonic()
        st.reviews_started += 1
        if plan.review_memory:
            st.memory_due = False
            st.turns_since_memory = 0
        if plan.review_skills:
            st.skills_due = False
            st.iters_since_skill = 0
            st.skill_refine_pending = False
        return True


def finish_review(agent_id: str | None, *, saved: bool = False) -> None:
    with _lock:
        st = get_state(agent_id)
        st.in_flight = False
        if saved:
            st.reviews_saved += 1


def peek_eligible(
    *,
    agent_id: str | None,
    tool_calls: int = 0,
    skills_touched: list[str] | None = None,
    nested: bool = False,
    ended_kind: str | None = "final",
) -> bool:
    """Non-mutating eligibility peek (tests / diagnostics)."""
    if nested or in_review_scope() or not learning_enabled():
        return False
    if ended_kind != "final":
        return False
    skills = [s for s in (skills_touched or []) if isinstance(s, str) and s.strip()]
    n_tools = max(0, int(tool_calls or 0))
    mem_n = memory_nudge_turns()
    skill_n = skill_nudge_iters()
    with _lock:
        st = get_state(agent_id)
        mem_due = st.memory_due or (
            mem_n > 0 and (st.turns_since_memory + 1) >= mem_n
        )
        skill_due = st.skills_due or (
            skill_n > 0
            and (
                (st.iters_since_skill + n_tools) >= skill_n or bool(skills)
            )
        )
        return bool(mem_due or skill_due)


def snapshot(agent_id: str | None = None) -> dict[str, Any]:
    with _lock:
        if agent_id is not None:
            st = get_state(agent_id)
            return {
                "agent_id": _key(agent_id),
                "turns_since_memory": st.turns_since_memory,
                "iters_since_skill": st.iters_since_skill,
                "memory_due": st.memory_due,
                "skills_due": st.skills_due,
                "in_flight": st.in_flight,
                "reviews_started": st.reviews_started,
                "reviews_saved": st.reviews_saved,
                "skipped_cooldown": st.reviews_skipped_cooldown,
                "skipped_inflight": st.reviews_skipped_inflight,
                "memory_nudge": memory_nudge_turns(),
                "skill_nudge": skill_nudge_iters(),
                "cooldown_sec": cooldown_seconds(),
            }
        return {k: snapshot(k) for k in list(_agents.keys())}


def reset_learning_state() -> None:
    """Test helper — clear all counters / hydration."""
    with _lock:
        _agents.clear()
        _hydrated_sessions.clear()
    _in_review.set(False)


# Back-compat alias used by older tests
def reset_learning_cooldowns() -> None:
    reset_learning_state()


__all__ = [
    "ReviewPlan",
    "AgentLearningState",
    "learning_enabled",
    "memory_nudge_turns",
    "skill_nudge_iters",
    "cooldown_seconds",
    "get_state",
    "enter_review_scope",
    "exit_review_scope",
    "in_review_scope",
    "hydrate_from_session",
    "observe_turn",
    "begin_review",
    "finish_review",
    "peek_eligible",
    "snapshot",
    "reset_learning_state",
    "reset_learning_cooldowns",
]
