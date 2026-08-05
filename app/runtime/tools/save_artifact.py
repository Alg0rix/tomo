"""``save_artifact`` — save files into the *session* artifacts directory."""

from __future__ import annotations

import json
from typing import Any

from app.runtime.artifacts.fs import (
    artifact_public_url,
    current_session_id,
    safe_session_id,
    validate_filename,
    write_artifact_bytes,
    write_artifact_text,
)
from app.runtime.artifacts.io import delete_source, is_under_attachments, read_source_bytes
from app.runtime.tools.sandbox import current_agent_id


def _resolve_session(arguments: dict[str, Any]) -> str | None:
    return safe_session_id(
        str(arguments.get("session_id") or current_session_id() or "").strip()
    )


def _catalog(
    *,
    session_id: str,
    agent_id: str,
    filename: str,
    filepath: str,
    size: int,
    notes: str = "",
    preview: str = "",
) -> None:
    try:
        from app.services import store

        art = store.create_artifact(
            {
                "title": filename,
                "path": filepath,
                "kind": "export",
                "notes": notes or f"size={size}",
                "session_id": session_id,
                "agent_id": agent_id,
            }
        )
        # Learning OS execution index (best-effort).
        snip = (preview or "").strip()
        if not snip:
            snip = f"{filename} ({notes or f'size={size}'})"
        store.insert_execution_snippet(
            session_id=session_id,
            agent_id=agent_id,
            source="artifact",
            ref_id=str(art.get("id") or ""),
            title=filename,
            snippet=snip[:2000],
            tags=["execution", "artifact"],
        )
    except Exception:
        pass


def run(arguments: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"

    session_id = _resolve_session(arguments)
    agent_id = str(arguments.get("agent_id") or current_agent_id() or "").strip()

    # Legacy catalog-only: title + path (no filename).
    filename = str(arguments.get("filename") or "").strip()
    if not filename and arguments.get("title") and arguments.get("path"):
        title = str(arguments.get("title") or "").strip()
        path = str(arguments.get("path") or "").strip()
        kind = str(arguments.get("kind") or "file").strip() or "file"
        notes = str(arguments.get("notes") or "").strip()
        from app.services import store

        try:
            art = store.create_artifact(
                {
                    "title": title,
                    "path": path,
                    "kind": kind,
                    "notes": notes,
                    "session_id": session_id or "",
                    "agent_id": agent_id,
                }
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Saved artifact '{art['id']}': {art['title']}" + (
            f" → {art['path']}" if art.get("path") else ""
        )

    if not session_id:
        return (
            "Error: session_id is required — artifacts are scoped to the current "
            "chat session (not the agent)."
        )

    err = validate_filename(filename)
    if err:
        return f"Error: {err}"

    content = arguments.get("content")
    source_path = str(arguments.get("source_path") or "").strip()
    mime_type = str(arguments.get("mime_type") or "").strip()

    try:
        if source_path:
            try:
                data, local_path = read_source_bytes(source_path, agent_id or None)
            except FileNotFoundError:
                looks_like_text = (
                    "\n" in source_path
                    or len(source_path) > 512
                    or (
                        not any(c in source_path for c in "/.\\")
                        and " " in source_path
                    )
                )
                if looks_like_text:
                    return (
                        "Error: Source file not found. The provided source_path "
                        "looks like text content — use the content parameter instead."
                    )
                return (
                    f'Error: Source file not found: "{source_path}". '
                    "If you meant to write text, use content instead of source_path."
                )
            except ValueError as exc:
                return f"Error: {exc}"

            info = write_artifact_bytes(session_id, filename, data)
            warning = None
            if local_path is not None and is_under_attachments(local_path):
                pass
            elif info["size"] == len(data):
                warning = delete_source(source_path, agent_id or None)
            else:
                info = write_artifact_bytes(session_id, filename, data)
                if info["size"] != len(data):
                    try:
                        from app.runtime.artifacts.fs import delete_artifact_file

                        delete_artifact_file(session_id, filename)
                    except Exception:
                        pass
                    return (
                        f"Error: Size mismatch after write "
                        f"({len(data)} vs {info['size']}). Source retained."
                    )
                warning = delete_source(source_path, agent_id or None)

            notes = f"mime={mime_type}" if mime_type else ""
            _catalog(
                session_id=session_id,
                agent_id=agent_id,
                filename=filename,
                filepath=info["filepath"],
                size=info["size"],
                notes=notes,
            )
            out = {
                "result": "Artifact saved successfully",
                "filepath": info["filepath"],
                "filename": filename,
                "size": info["size"],
                "session_id": session_id,
                "category": info.get("category") or "",
                "url": info["url"],
            }
            if warning:
                out["warning"] = warning
            return json.dumps(out, ensure_ascii=False)

        if not isinstance(content, str) or content == "":
            return (
                "Error: No data provided. Use content for text "
                '(e.g. content="# Report\\n...") or source_path for an existing file.'
            )

        info = write_artifact_text(session_id, filename, content)
        notes = f"mime={mime_type}" if mime_type else ""
        _catalog(
            session_id=session_id,
            agent_id=agent_id,
            filename=filename,
            filepath=info["filepath"],
            size=info["size"],
            notes=notes,
            preview=content[:2000],
        )
        return json.dumps(
            {
                "result": "Artifact saved successfully",
                "filepath": info["filepath"],
                "filename": filename,
                "size": info["size"],
                "session_id": session_id,
                "category": info.get("category") or "",
                "url": artifact_public_url(session_id, filename),
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return f'Error: Failed to save artifact "{filename}": {exc}'


__all__ = ["run"]
