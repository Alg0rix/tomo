"""Production episodic memory service — build, persist, retrieve, feedback.

Sits above :mod:`app.models.mixins.episodic` with orchestration used by tools,
learning review, and turn retrieval.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


def record_experience(data: dict[str, Any]) -> dict[str, Any] | None:
    """Persist a structured experience (deduped, scored, embedded)."""
    from app.services import store

    return store.insert_episode(data)


def build_from_review(
    *,
    user_id: str,
    agent_id: str | None,
    session_id: str | None,
    user_message: str | None,
    final_content: str | None,
    tool_calls: int = 0,
    diary: str | None = None,
    actions: list[str] | None = None,
    note: str | None = None,
) -> dict[str, Any] | None:
    """Auto-create an episode after a learning review when experience is clear.

    Selective: skips trivial turns (no tools, no diary, empty finals).
    """
    goal = (user_message or "").strip()
    final = (final_content or "").strip()
    diary_s = (diary or "").strip()
    note_s = (note or "").strip()
    acts = [a for a in (actions or []) if str(a).strip()]

    # Admission heuristics — not every review becomes an episode.
    if tool_calls <= 0 and not diary_s and len(final) < 40:
        return None
    if not goal and not final and not diary_s:
        return None

    importance = 0.55
    if tool_calls >= 3:
        importance += 0.15
    if diary_s:
        importance += 0.1
    if any("Error" in str(a) or "fail" in str(a).lower() for a in acts):
        importance += 0.1
    importance = min(0.95, importance)

    status = "success"
    if any("fail" in str(a).lower() or "error" in str(a).lower() for a in acts):
        if not final or "error" in final.lower():
            status = "partial" if final else "failure"

    trajectory = ""
    if acts:
        trajectory = "; ".join(str(a)[:120] for a in acts[:12])
    elif note_s:
        trajectory = note_s[:1500]

    data = {
        "user_id": user_id or "web",
        "agent_id": agent_id or "",
        "session_id": session_id or "",
        "title": (goal or diary_s or "Experience")[:80],
        "trigger": {
            "type": "request",
            "source": "user",
            "description": goal[:500] if goal else "turn completed",
        },
        "objective": goal or diary_s or "Complete the turn objective",
        "trajectory_summary": trajectory or final[:1500],
        "actions": acts[:20],
        "outcome_status": status,
        "outcome_summary": (final or diary_s or "Completed")[:2000],
        "reflection_summary": diary_s or note_s[:1000],
        "importance": importance,
        "confidence": 0.7 if diary_s else 0.55,
        "utility": 0.65 if tool_calls else 0.45,
        "provenance": {
            "sources": [{"type": "learning_review", "session_id": session_id or ""}],
            "evidence": [],
        },
    }
    try:
        from app.services import store

        return store.insert_episode(data)
    except Exception as exc:
        _logger.debug("build_from_review failed: %s", exc)
        return None


def retrieve_for_situation(
    query: str,
    *,
    user_id: str,
    workplace_id: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    from app.services import store

    return store.search_episodes(
        query,
        user_id=user_id,
        workplace_id=workplace_id,
        limit=limit,
    )


def feedback(episode_id: str, *, helpful: bool, user_id: str | None = None) -> bool:
    from app.models.mixins import episodic as ep
    from app.services import store

    with store._lock:
        return ep.record_retrieval_feedback(
            store._conn, episode_id, helpful=helpful, user_id=user_id
        )


def run_decay(*, user_id: str | None = None) -> dict[str, int]:
    from app.models.mixins import episodic as ep
    from app.services import store

    with store._lock:
        return ep.apply_decay(store._conn, user_id=user_id)


__all__ = [
    "record_experience",
    "build_from_review",
    "retrieve_for_situation",
    "feedback",
    "run_decay",
]
