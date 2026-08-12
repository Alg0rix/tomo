"""``record_episode`` tool — persist a structured episodic experience."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: record_episode expects a dict of arguments"

    # Accept structured fields and/or freeform content.
    has_structure = any(
        arguments.get(k)
        for k in (
            "objective",
            "outcome",
            "outcome_summary",
            "trajectory",
            "trajectory_summary",
            "reflection",
            "reflection_summary",
            "content",
            "body",
            "story",
        )
    )
    if not has_structure:
        return (
            "Error: provide an experience — at least one of: content (freeform), "
            "objective, outcome/outcome_summary, trajectory, or reflection"
        )

    from app.runtime.tools.sandbox import current_agent_id
    from app.runtime.tools.user_ctx import current_user_id
    from app.services import store

    session_id = ""
    try:
        from app.runtime.artifacts.fs import current_session_id

        session_id = (current_session_id() or "").strip()
    except Exception:
        session_id = ""

    workplace_id = ""
    try:
        from app.runtime.tools.workplace_ctx import current_workplace_id

        workplace_id = (current_workplace_id() or "").strip()
    except Exception:
        workplace_id = ""

    payload = dict(arguments)
    payload.setdefault("user_id", current_user_id())
    payload.setdefault("agent_id", current_agent_id() or "")
    payload.setdefault("session_id", session_id)
    if workplace_id:
        payload.setdefault("workplace_id", workplace_id)

    try:
        ep = store.insert_episode(payload)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: could not record episode: {exc}"

    if not ep:
        return "Error: could not record episode (empty experience?)"
    score = ep.get("memory_score")
    state = ep.get("state") or "active"
    label = ep.get("title") or ep.get("objective") or ep.get("id")
    score_s = f" score={score:.2f}" if isinstance(score, (int, float)) else ""
    return (
        f"Recorded episode {ep['id']} [{state}]{score_s}: {str(label)[:140]}"
    )


__all__ = ["run"]
