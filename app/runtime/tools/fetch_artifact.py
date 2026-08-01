"""``fetch_artifact`` — copy a session artifact into the workplace/sandbox."""

from __future__ import annotations

import json
from typing import Any

from app.runtime.artifacts.fs import (
    artifacts_dir,
    current_session_id,
    read_artifact_bytes,
    safe_session_id,
    validate_filename,
)
from app.runtime.artifacts.io import agent_has_remote_workplace, write_dest_bytes
from app.runtime.tools.sandbox import current_agent_id, resolve_work_root


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"

    filename = str(arguments.get("filename") or "").strip()
    err = validate_filename(filename)
    if err:
        return f"Error: {err}"

    session_id = safe_session_id(
        str(arguments.get("session_id") or current_session_id() or "").strip()
    )
    if not session_id:
        return (
            "Error: session_id is required — artifacts are scoped to the current "
            "chat session."
        )

    agent_id = str(arguments.get("agent_id") or current_agent_id() or "").strip()
    dest_path = str(arguments.get("dest_path") or "").strip()

    try:
        data = read_artifact_bytes(session_id, filename)
    except FileNotFoundError:
        return (
            f'Error: Artifact not found: "{filename}". '
            "Use list_artifacts to see available files in this session."
        )
    except ValueError as exc:
        return f"Error: {exc}"

    if not agent_has_remote_workplace(agent_id or None) and not dest_path:
        host_path = str(artifacts_dir(session_id) / filename)
        return json.dumps(
            {
                "result": "Local agent can access session artifacts directly.",
                "filepath": host_path,
                "filename": filename,
                "size": len(data),
                "session_id": session_id,
                "hint": (
                    f"This file is at: {host_path}. "
                    "Use read_file, bash, or runpy to access it directly, "
                    "or pass dest_path to copy into the work dir."
                ),
            },
            ensure_ascii=False,
        )

    if not dest_path:
        dest_path = filename

    try:
        written = write_dest_bytes(dest_path, data, agent_id or None)
    except Exception as exc:
        return f'Error: Failed to fetch artifact "{filename}": {exc}'

    return json.dumps(
        {
            "result": "Artifact fetched successfully",
            "filepath": written,
            "filename": filename,
            "size": len(data),
            "session_id": session_id,
            "work_root": str(resolve_work_root(agent_id or None)),
        },
        ensure_ascii=False,
    )


__all__ = ["run"]
