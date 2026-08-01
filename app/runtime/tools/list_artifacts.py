"""``list_artifacts`` — list/search the *session* artifacts directory."""

from __future__ import annotations

import json
from typing import Any

from app.runtime.artifacts.fs import current_session_id, list_artifact_files, safe_session_id


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"
    session_id = safe_session_id(
        str(arguments.get("session_id") or current_session_id() or "").strip()
    )
    if not session_id:
        return (
            "Error: session_id is required — artifacts are scoped to the current "
            "chat session."
        )

    result = list_artifact_files(
        session_id,
        filter=str(arguments.get("filter") or ""),
        grep=str(arguments.get("grep") or ""),
        type=str(arguments.get("type") or ""),
        sort=str(arguments.get("sort") or "newest"),
        limit=arguments.get("limit", 50) if arguments.get("limit") is not None else 50,
    )
    return json.dumps(result, ensure_ascii=False)


__all__ = ["run"]
