"""``record_episode`` tool — persist a concrete past experience."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: record_episode expects a dict of arguments"

    title = arguments.get("title")
    tried = arguments.get("tried") or arguments.get("action") or ""
    context = arguments.get("context") or ""
    error = arguments.get("error") or arguments.get("problem") or ""
    fix = arguments.get("fix") or arguments.get("resolution") or ""
    outcome = arguments.get("outcome") or arguments.get("result") or ""
    summary = arguments.get("summary") or ""

    if not isinstance(title, str) or not title.strip():
        # Allow title-less if tried+outcome present.
        if not (isinstance(tried, str) and tried.strip()):
            return "Error: provide 'title' or 'tried' describing the episode"
        title = str(tried).strip()[:80]

    from app.runtime.tools.sandbox import current_agent_id
    from app.runtime.tools.user_ctx import current_user_id
    from app.services import store

    session_id = ""
    try:
        from app.runtime.artifacts.fs import current_session_id

        session_id = (current_session_id() or "").strip()
    except Exception:
        session_id = ""

    try:
        ep = store.insert_episode(
            {
                "user_id": current_user_id(),
                "session_id": session_id or "",
                "agent_id": current_agent_id() or "",
                "title": str(title).strip(),
                "tried": str(tried or "").strip(),
                "context": str(context or "").strip(),
                "error": str(error or "").strip(),
                "fix": str(fix or "").strip(),
                "outcome": str(outcome or "").strip(),
                "summary": str(summary or "").strip(),
            }
        )
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: could not record episode: {exc}"

    if not ep:
        return "Error: could not record episode (empty content?)"
    return (
        f"Recorded episode {ep['id']}: {ep.get('title') or ep.get('summary', '')[:120]}"
    )


__all__ = ["run"]
