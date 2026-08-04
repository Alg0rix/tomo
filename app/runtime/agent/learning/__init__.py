"""Active learning harness — observe → distill → reuse → refine.

After eligible top-level turns, a background review may write memory /
skills / artifacts. The main chat turn is never blocked.

Trigger model (counters, not keyword gates on user text):

* **Memory** — every N successful top-level turns (``learning_memory_nudge_turns``)
* **Skills** — cumulative tool iterations across turns
  (``learning_skill_nudge_iters``) or a skill touched this turn (refine)

Hardening:

* Sticky dues survive cooldown / in-flight skips (nudge is not burned)
* Skill iters accumulate across turns (not only "tools on this turn")
* Review runs in an isolation scope (no recursive observe, agent sandbox bound)
* Optional cheaper review profile via ``learning_review_profile_id``
* Structured digest (goal, trail, skills, final) instead of full replay
"""

from __future__ import annotations

from app.runtime.agent.learning.bond import compute_bond
from app.runtime.agent.learning.companion import companion_snapshot
from app.runtime.agent.learning.diary import derive_diary, extract_diary_line
from app.runtime.agent.learning.digest import build_review_digest, compact_tool_trail
from app.runtime.agent.learning.runner import run_learning_review, schedule_learning_review
from app.runtime.agent.learning.state import (
    ReviewPlan,
    cooldown_seconds,
    hydrate_from_session,
    learning_enabled,
    memory_nudge_turns,
    observe_turn,
    peek_eligible,
    reset_learning_cooldowns,
    reset_learning_state,
    skill_nudge_iters,
    snapshot,
)



def decide_review(
    *,
    metrics,
    skills_touched: list[str] | None = None,
    nested: bool = False,
) -> dict[str, bool]:
    """Advance counters and return ``{review_memory, review_skills}``.

    Prefer :func:`observe_turn` / :func:`schedule_learning_review` in new code.
    This wrapper keeps older call sites working.
    """
    plan = observe_turn(
        agent_id=getattr(metrics, "agent_id", None),
        tool_calls=int(getattr(metrics, "tool_calls", 0) or 0),
        skills_touched=skills_touched,
        nested=nested,
        ended_kind=getattr(metrics, "ended_kind", None),
    )
    if plan is None:
        return {"review_memory": False, "review_skills": False}
    return {
        "review_memory": plan.review_memory,
        "review_skills": plan.review_skills,
    }


def is_learning_eligible(
    *,
    metrics,
    skills_touched: list[str] | None = None,
    nested: bool = False,
) -> bool:
    """Peek whether a review would fire — does not advance turn counters."""
    return peek_eligible(
        agent_id=getattr(metrics, "agent_id", None),
        tool_calls=int(getattr(metrics, "tool_calls", 0) or 0),
        skills_touched=skills_touched,
        nested=nested,
        ended_kind=getattr(metrics, "ended_kind", None),
    )


__all__ = [
    "ReviewPlan",
    "learning_enabled",
    "memory_nudge_turns",
    "skill_nudge_iters",
    "cooldown_seconds",
    "hydrate_from_session",
    "observe_turn",
    "decide_review",
    "is_learning_eligible",
    "peek_eligible",
    "compact_tool_trail",
    "build_review_digest",
    "run_learning_review",
    "schedule_learning_review",
    "reset_learning_cooldowns",
    "reset_learning_state",
    "snapshot",
    "compute_bond",
    "companion_snapshot",
    "derive_diary",
    "extract_diary_line",
]
