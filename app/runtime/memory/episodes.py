"""Production + Phase 3 episodic memory orchestration (SQLite only).

No vector embeddings. Features:
- Experience graph (relations + auto-link)
- Contradiction analysis
- Semantic consolidation → knowledge_entries
- Procedural extraction → knowledge tags procedural
- Learned retrieval ranking (feedback-driven weights)
- Utility learning via retrieval feedback
- Automatic episode boundaries (session open/close)
- Cross-agent retrieval (user-scoped)
- LTM optimization (decay, consolidate, archive)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

_logger = logging.getLogger(__name__)

# session_id -> open episode id (process-local boundary tracker)
_OPEN_EPISODES: dict[str, str] = {}


def record_experience(data: dict[str, Any]) -> dict[str, Any] | None:
    """Persist a structured experience (deduped, scored, graph-linked)."""
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
    """Auto-create/close an episode after learning review (selective)."""
    goal = (user_message or "").strip()
    final = (final_content or "").strip()
    diary_s = (diary or "").strip()
    note_s = (note or "").strip()
    acts = [a for a in (actions or []) if str(a).strip()]

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

    parent = ""
    sid = (session_id or "").strip()
    if sid and sid in _OPEN_EPISODES:
        parent = _OPEN_EPISODES.get(sid) or ""

    data = {
        "user_id": user_id or "web",
        "agent_id": agent_id or "",
        "session_id": session_id or "",
        "parent_episode_id": parent,
        "title": (goal or diary_s or "Experience")[:80],
        "trigger": {
            "type": "request",
            "source": "user",
            "description": goal[:500] if goal else "turn completed",
        },
        "objective": goal or diary_s or "Complete the turn objective",
        "trajectory_summary": trajectory or final[:1500],
        "actions": acts[:20],
        "events": [
            {"type": "action", "description": str(a)[:400], "sequence": i}
            for i, a in enumerate(acts[:20])
        ],
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

        ep = store.insert_episode(data)
        if ep and sid:
            # Boundary: close session episode on terminal outcome.
            if status in {"success", "failure", "abandoned"}:
                close_episode(sid)
            else:
                _OPEN_EPISODES[sid] = ep["id"]
        return ep
    except Exception as exc:
        _logger.debug("build_from_review failed: %s", exc)
        return None


# ── Automatic episode boundaries ──────────────────────────────────────


def open_episode(
    *,
    session_id: str,
    user_id: str,
    agent_id: str = "",
    objective: str = "",
    context_summary: str = "",
    workplace_id: str = "",
) -> dict[str, Any] | None:
    """Start a coherent experience for a session (boundary start)."""
    sid = (session_id or "").strip()
    if not sid:
        return None
    if sid in _OPEN_EPISODES:
        from app.services import store
        from app.models.mixins import episodic as ep

        with store._lock:
            existing = ep.get_episode(
                store._conn, _OPEN_EPISODES[sid], user_id=user_id
            )
        if existing and existing.get("state") not in {"archived", "superseded"}:
            return existing

    data = {
        "user_id": user_id or "web",
        "agent_id": agent_id or "",
        "session_id": sid,
        "workplace_id": workplace_id or "",
        "title": (objective or "In progress")[:80],
        "trigger": {"type": "request", "source": "user", "description": objective[:500]},
        "objective": objective or "Session task",
        "context_summary": context_summary,
        "outcome_status": "unknown",
        "outcome_summary": "in progress",
        "state": "candidate",
        "importance": 0.4,
        "confidence": 0.4,
        "utility": 0.4,
        "force": True,
    }
    from app.services import store

    ep = store.insert_episode(data)
    if ep:
        _OPEN_EPISODES[sid] = ep["id"]
    return ep


def append_to_open(
    session_id: str,
    *,
    event: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> bool:
    """Append a trajectory event to the session's open episode."""
    sid = (session_id or "").strip()
    eid = _OPEN_EPISODES.get(sid)
    if not eid or not event:
        return False
    from app.services import store

    return store.append_episode_event(eid, event) is not None


def close_episode(
    session_id: str,
    *,
    outcome_status: str = "success",
    outcome_summary: str = "",
    reflection: str = "",
) -> dict[str, Any] | None:
    """Close the open episode for a session (boundary end)."""
    sid = (session_id or "").strip()
    eid = _OPEN_EPISODES.pop(sid, None)
    if not eid:
        return None
    from app.models.mixins import episodic as ep
    from app.services import store

    with store._lock:
        row = ep.get_episode(store._conn, eid)
        if not row:
            return None
        # Patch outcome into payload via re-insert is heavy; update columns.
        conn = store._conn
        cols = {r[1] for r in conn.execute("PRAGMA table_info(episodic_memories)")}
        if "outcome_status" in cols:
            conn.execute(
                """
                UPDATE episodic_memories
                SET outcome_status=?, outcome_summary=?,
                    reflection_summary=CASE WHEN ?!='' THEN ? ELSE reflection_summary END,
                    state='active', ended_at=?
                WHERE id=?
                """,
                (
                    outcome_status,
                    outcome_summary or row.get("outcome_summary") or "",
                    reflection,
                    reflection,
                    time.time(),
                    eid,
                ),
            )
            conn.commit()
        return ep.get_episode(conn, eid)


def active_episode_id(session_id: str) -> str | None:
    return _OPEN_EPISODES.get((session_id or "").strip())


# ── Retrieval / feedback ──────────────────────────────────────────────


def retrieve_for_situation(
    query: str,
    *,
    user_id: str,
    workplace_id: str | None = None,
    agent_id: str | None = None,
    cross_agent: bool = True,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Cross-agent by default (same user), SQLite lexical + graph expand."""
    from app.services import store

    return store.search_episodes(
        query,
        user_id=user_id,
        workplace_id=workplace_id,
        limit=limit,
    )


def feedback(episode_id: str, *, helpful: bool, user_id: str | None = None) -> bool:
    from app.services import store

    return store.episode_feedback(
        episode_id, helpful=helpful, user_id=user_id
    )


def run_decay(*, user_id: str | None = None) -> dict[str, int]:
    from app.services import store

    return store.decay_episodes(user_id=user_id)


# ── Contradiction analysis ────────────────────────────────────────────


def contradictions(
    *, user_id: str, episode_id: str | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    from app.models.mixins import episodic as ep
    from app.services import store

    with store._lock:
        base = None
        if episode_id:
            base = ep.get_episode(store._conn, episode_id, user_id=user_id)
        return ep.find_contradictions(
            store._conn, user_id=user_id, episode=base, limit=limit
        )


# ── Semantic consolidation ────────────────────────────────────────────


def consolidate_semantic(
    *, user_id: str, limit: int = 8, min_reuse: int = 2
) -> list[dict[str, Any]]:
    """Mine durable facts from high-utility episodes → knowledge_entries.

    Procedural-looking lessons stay tagged; general lessons become semantic.
    Marks source episodes consolidated when a write succeeds.
    """
    from app.services import store

    eps = store.list_episodes(user_id=user_id, state="active", limit=80)
    # Prefer high reuse / importance failures-and-successes with lessons.
    ranked = sorted(
        eps,
        key=lambda e: (
            -(int(e.get("reuse_success") or 0)),
            -(float(e.get("importance") or 0)),
            -(float(e.get("utility") or 0)),
        ),
    )
    written: list[dict[str, Any]] = []
    for e in ranked:
        if len(written) >= limit:
            break
        if int(e.get("reuse_success") or 0) < min_reuse and float(
            e.get("importance") or 0
        ) < 0.85:
            continue
        lessons = []
        payload = e.get("payload") or {}
        refl = payload.get("reflection") or {}
        for lesson in refl.get("lessons") or []:
            if isinstance(lesson, dict):
                s = (lesson.get("statement") or "").strip()
            else:
                s = str(lesson or "").strip()
            if s:
                lessons.append(s)
        if not lessons and (e.get("reflection_summary") or "").strip():
            lessons = [e["reflection_summary"].strip()[:400]]
        if not lessons:
            continue
        # Skip pure how-to that belongs in procedural extraction.
        for lesson in lessons[:2]:
            tags = ["from-episodic", "consolidated"]
            low = lesson.lower()
            if any(
                w in low
                for w in ("step", "always", "first", "then ", "procedure", "workflow")
            ):
                tags.append("procedural")
            else:
                tags.append("semantic")
            title = (e.get("title") or e.get("objective") or "Lesson")[:80]
            try:
                entry = store.create_knowledge_entry(
                    {
                        "title": f"From experience: {title}",
                        "body": (
                            f"{lesson}\n\n"
                            f"(Source episode {e.get('id')}; "
                            f"outcome={e.get('outcome_status')}; "
                            f"context={ (e.get('context_summary') or '')[:200] })"
                        ),
                        "tags": tags,
                        "user_id": user_id,
                        "confidence": min(
                            0.95, 0.55 + 0.1 * int(e.get("reuse_success") or 0)
                        ),
                    }
                )
                written.append(
                    {"knowledge_id": entry.get("id"), "episode_id": e.get("id"), "tags": tags}
                )
                # Mark episode consolidated when durable fact extracted.
                from app.models.mixins import episodic as ep_mod

                with store._lock:
                    ep_mod.set_episode_state(
                        store._conn, e["id"], "consolidated", user_id=user_id
                    )
            except Exception as exc:
                _logger.debug("semantic consolidate failed: %s", exc)
    return written


def extract_procedures(
    *, user_id: str, limit: int = 5, min_success: int = 1
) -> list[dict[str, Any]]:
    """Extract procedural patterns from successful trajectories → knowledge.

    Uses per-user knowledge with tag ``procedural`` (not global skills catalog).
    Includes ``consolidated`` episodes so LTM optimize can run semantic then procedural.
    """
    from app.services import store

    # state=None → active + candidate + consolidated (excludes archived/superseded).
    eps = store.list_episodes(user_id=user_id, state=None, limit=80)
    successes = [
        e
        for e in eps
        if (e.get("outcome_status") or "") == "success"
        and (
            int(e.get("reuse_success") or 0) >= min_success
            or float(e.get("importance") or 0) >= 0.75
        )
    ]
    written: list[dict[str, Any]] = []
    for e in successes:
        if len(written) >= limit:
            break
        traj = (e.get("trajectory_summary") or "").strip()
        actions = (e.get("payload") or {}).get("trajectory", {}).get("actions") or []
        if not traj and not actions:
            continue
        steps: list[str] = []
        if isinstance(actions, list):
            for a in actions:
                if isinstance(a, str) and a.strip():
                    steps.append(a.strip())
                elif isinstance(a, dict) and a.get("description"):
                    steps.append(str(a["description"]).strip())
        if not steps and traj:
            # Split trajectory on common separators into pseudo-steps.
            steps = [
                p.strip()
                for p in re.split(r"[;\n]|(?:\.\s+)", traj)
                if len(p.strip()) > 8
            ][:12]
        if len(steps) < 2:
            continue
        body = (
            f"When: {e.get('objective') or e.get('title') or 'similar situation'}\n"
            f"Context: {e.get('context_summary') or 'n/a'}\n"
            f"Procedure:\n"
            + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps[:12]))
            + f"\n\nOutcome: {e.get('outcome_summary') or 'success'}"
            + f"\n(Source episode {e.get('id')})"
        )
        try:
            entry = store.create_knowledge_entry(
                {
                    "title": f"Procedure: {(e.get('title') or e.get('objective') or 'task')[:60]}",
                    "body": body,
                    "tags": ["procedural", "from-episodic", "consolidated"],
                    "user_id": user_id,
                    "confidence": 0.75,
                }
            )
            written.append(
                {"knowledge_id": entry.get("id"), "episode_id": e.get("id")}
            )
        except Exception as exc:
            _logger.debug("procedure extract failed: %s", exc)
    return written


# ── Long-term memory optimization ─────────────────────────────────────


def optimize_ltm(*, user_id: str) -> dict[str, Any]:
    """Run decay + consolidation + procedure extraction for one account."""
    decay = run_decay(user_id=user_id)
    semantic = consolidate_semantic(user_id=user_id)
    procedures = extract_procedures(user_id=user_id)
    contras = contradictions(user_id=user_id, limit=20)
    return {
        "decay": decay,
        "semantic_facts": len(semantic),
        "procedures": len(procedures),
        "contradictions": len(contras),
        "details": {
            "semantic": semantic,
            "procedures": procedures,
            "contradictions": contras[:10],
        },
    }


__all__ = [
    "record_experience",
    "build_from_review",
    "open_episode",
    "append_to_open",
    "close_episode",
    "active_episode_id",
    "retrieve_for_situation",
    "feedback",
    "run_decay",
    "contradictions",
    "consolidate_semantic",
    "extract_procedures",
    "optimize_ltm",
]
