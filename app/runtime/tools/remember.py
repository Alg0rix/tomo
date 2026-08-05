"""``remember`` tool — persist a fact into SQLite knowledge entries."""

from __future__ import annotations

from typing import Any


def run(arguments: dict[str, Any]) -> str:
    """Tool backend entry point used by the registry."""
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    title = arguments.get("title")
    body = arguments.get("body")
    if not isinstance(title, str) or not title.strip():
        return "Error: 'title' argument must be a non-empty string"
    if not isinstance(body, str) or not body.strip():
        return "Error: 'body' argument must be a non-empty string"
    tags = arguments.get("tags")
    if tags is None:
        tags_list: list[str] = []
    elif isinstance(tags, list):
        tags_list = [str(t).strip() for t in tags if str(t).strip()]
    elif isinstance(tags, str):
        tags_list = [p.strip() for p in tags.split(",") if p.strip()]
    else:
        return "Error: 'tags' must be an array of strings"

    confidence: float | None = None
    if "confidence" in arguments and arguments["confidence"] is not None:
        try:
            confidence = float(arguments["confidence"])
        except (TypeError, ValueError):
            return "Error: 'confidence' must be a number"
    else:
        try:
            from app.runtime.agent.learning.state import in_review_scope

            if in_review_scope():
                confidence = 0.9
        except Exception:
            confidence = None

    from app.services import store

    payload: dict[str, Any] = {
        "title": title.strip(),
        "body": body.strip(),
        "tags": tags_list,
    }
    if confidence is not None:
        payload["confidence"] = confidence
        payload["success_count"] = 1  # review-confirmed or explicit confidence

    try:
        entry = store.create_knowledge_entry(payload)
    except ValueError as exc:
        return f"Error: {exc}"
    conf = entry.get("confidence")
    conf_bit = f" (confidence={conf:.2f})" if isinstance(conf, (int, float)) else ""
    return f"Saved knowledge entry '{entry['id']}': {entry['title']}{conf_bit}"


__all__ = ["run"]
