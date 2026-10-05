"""vision_analyze tool — describe an image the agent can't otherwise see.

Sources: a workplace-relative (or in-root absolute) file path, an
``attachment:<id>`` reference to a chat attachment, an http(s) URL
(SSRF-guarded, size-capped), or a ``data:`` URL. The image is described by
the resolved auxiliary vision profile (explicit ``vision_profile_id`` pin →
the agent's own model when vision-capable → first vision-capable enabled
profile) and returned as text.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any

_MAX_SOURCE_BYTES = 20 * 1024 * 1024
_DOWNLOAD_TIMEOUT = 30.0

_ATTACH_PREFIX = "attachment:"


def _data_url_bytes(url: str) -> bytes | str:
    header, _, data = url.partition(",")
    if ";base64" not in header:
        return "Error: only base64 data: URLs are supported"
    if len(data) > _MAX_SOURCE_BYTES * 4 // 3:
        return "Error: data URL payload too large"
    try:
        return base64.b64decode(data)
    except Exception:
        return "Error: could not decode base64 data URL"


def _download_bytes(url: str) -> bytes | str:
    """SSRF-guarded image download; error string on refusal."""
    from app.runtime.tools.web_fetch import _check_url

    blocked = _check_url(url)
    if blocked:
        return blocked
    try:
        import httpx2

        with httpx2.Client(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=False) as client:
            resp = client.get(url)
        if resp.status_code != 200:
            return f"Error: image download failed (HTTP {resp.status_code})"
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype and not ctype.startswith("image/") and ctype != "application/octet-stream":
            return f"Error: URL did not return an image (content-type {ctype})"
        if len(resp.content) > _MAX_SOURCE_BYTES:
            return "Error: image download too large (max 20 MB)"
        return resp.content
    except Exception as exc:
        return f"Error: image download failed: {exc}"


def _attachment_bytes(attachment_id: str) -> tuple[bytes | None, str | None, str | None]:
    """``(raw, mime, error)`` for a stored chat attachment."""
    from app.runtime.access import current_execution
    from app.services import store

    att = store.get_attachment(attachment_id)
    if not att:
        return None, None, f"Error: attachment not found: {attachment_id}"
    execution = current_execution(required=False)
    if execution is not None:
        from app.services import store as _store

        execution = _store.access.revalidate(execution)
        if att.get("session_id") != execution.session_id:
            return None, None, f"Error: attachment not found: {attachment_id}"
    path = Path(att.get("file_path") or "")
    try:
        raw = path.read_bytes() if path.is_file() else None
    except OSError:
        raw = None
    if not raw:
        return None, None, f"Error: could not read attachment file: {attachment_id}"
    mime = att.get("mime_type") or mimetypes.guess_type(path.name)[0]
    return raw, mime, None


def _restricted_workplace_file(root: Path, source: str) -> Path | str:
    """Jail *source* under *root* for restricted preprocessing.

    Unlike :func:`jail_path` (explicitly unrestricted host helper), this
    resolves symlinks and then enforces containment: a link pointing outside
    the owned workplace root is rejected instead of followed.
    """
    text = (source or "").strip()
    if not text or "\x00" in text:
        return "Error: source must be a non-empty path"
    try:
        root_resolved = root.resolve()
        candidate = Path(text)
        if candidate.is_absolute():
            target = candidate.resolve()
        else:
            target = (root_resolved / text).resolve()
    except OSError as exc:
        return f"Error: invalid path: {exc}"
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return "Error: path escapes the working location"
    return target


def _local_path_bytes(source: str) -> tuple[bytes | None, str | None, str | None]:
    """``(raw, mime, error)`` for a workplace-jailed local path."""
    from app.runtime.access import current_execution
    from app.runtime.tools.sandbox import current_agent_id, jail_path, resolve_work_root
    from app.services import store as _store

    execution = _store.access.revalidate(current_execution())
    if execution.execution_mode == "unrestricted":
        root = resolve_work_root(current_agent_id())
        resolved = jail_path(root, source)
        if isinstance(resolved, str):
            return None, None, resolved  # jail_path already returns "Error: ..."
    else:
        # Restricted chats never touch host paths directly: read through the
        # owned active workplace root with symlink containment. Describe is
        # read-only; grants already checked.
        resource = next(
            (r for r in execution.resources if r.workplace_id == execution.active_workplace_id), None
        )
        if resource is None or resource.transfer_only or resource.kind != "local":
            return None, None, "Error: working location is unavailable for image input"
        resolved = _restricted_workplace_file(Path(resource.root_path), source)
        if isinstance(resolved, str):
            return None, None, resolved
    try:
        if not resolved.is_file():
            return None, None, f"Error: could not read {source}: not a file"
        if resolved.stat().st_size > _MAX_SOURCE_BYTES:
            return None, None, "Error: image file too large (max 20 MB)"
        raw = resolved.read_bytes()
    except OSError as exc:
        return None, None, f"Error: could not read {source}: {exc}"
    return raw, mimetypes.guess_type(resolved.name)[0], None


def _resolve_source(source: str) -> tuple[bytes | None, str | None, str | None]:
    """``(raw, mime, error)`` for any supported source form."""
    source = (source or "").strip()
    if not source:
        return None, None, "Error: source is required"
    if source.startswith(_ATTACH_PREFIX):
        return _attachment_bytes(source[len(_ATTACH_PREFIX):].strip())
    if source.startswith("data:"):
        raw = _data_url_bytes(source)
        if isinstance(raw, str):
            return None, None, raw
        mime = source[len("data:"):].split(";", 1)[0].strip() or None
        return raw, mime, None
    if source.startswith(("http://", "https://")):
        raw = _download_bytes(source)
        if isinstance(raw, str):
            return None, None, raw
        return raw, None, None
    return _local_path_bytes(source)


def _normalize_region(region: Any) -> list[int] | str | None:
    if region is None:
        return None
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        return "Error: region must be [x1, y1, x2, y2] in pixel coordinates"
    try:
        return [int(v) for v in region]
    except (TypeError, ValueError):
        return "Error: region must contain integer pixel coordinates"


def _require_assigned_vision_profile(context) -> None:
    """Raise fail-closed unless an assigned vision profile resolves.

    The auxiliary vision model is selected only from profiles the user is
    assigned (explicit pin, the agent's own vision-capable model, or the
    first assigned vision-capable profile). Unassigned/global models are
    never silently borrowed for another user's image bytes. Raising (not an
    error string) keeps direct-call authorization denials auditable and
    consistent with :func:`authorize_tool`.
    """
    from app.runtime.access import AccessUnavailable
    from app.runtime.llm.vision import lookup_vision_capability, resolve_vision_profile
    from app.services import store

    candidates: list[dict] = []
    try:
        pinned = resolve_vision_profile(context.agent_id)
        if pinned and pinned.get("id"):
            candidates.append(pinned)
    except Exception:
        pass
    try:
        for prof in store.list_llm_profiles():
            if not prof.get("enabled", True) or any(p["id"] == prof["id"] for p in candidates):
                continue
            if lookup_vision_capability(prof.get("model"), prof.get("base_url") or "") is True:
                candidates.append(prof)
    except Exception:
        pass
    for prof in candidates:
        try:
            store.access.require_use(context.user_id, "model", prof["id"])
            return
        except Exception:
            continue
    raise AccessUnavailable("No vision-capable model profile is available for this chat")


async def _describe(data_url: str, question: str, agent_id: str | None) -> str:
    from app.runtime.llm.vision import analyze_image_data_url

    prompt = (
        "Describe this image in enough detail to answer the request below. "
        "Include any readable text, code, UI, and data verbatim where it matters.\n\n"
        f"Request: {question.strip() or 'Describe this image.'}"
    )
    return await analyze_image_data_url(agent_id, data_url, prompt)


def run(arguments: dict[str, Any]) -> str:
    """Sync tool entry (registry dispatches on a worker thread)."""
    if not isinstance(arguments, dict):
        return "Error: vision_analyze expects a dict of arguments"
    from app.runtime.policy import authorize_tool
    context = authorize_tool("vision_analyze", arguments)
    _require_assigned_vision_profile(context)
    source = str(arguments.get("source") or arguments.get("image_url") or "").strip()
    question = str(arguments.get("question") or "")
    region = _normalize_region(arguments.get("region"))
    if isinstance(region, str):
        return region

    raw, mime, error = _resolve_source(source)
    if error:
        return error
    try:
        from app.runtime.llm.vision_image import encode_image_data_url

        data_url, note = encode_image_data_url(raw or b"", mime, region=region)
    except ValueError as exc:
        return f"Error: {exc}"

    from app.runtime.tools.sandbox import current_agent_id

    try:
        description = asyncio.run(_describe(data_url, question, current_agent_id()))
    except Exception as exc:
        return f"Error: vision analysis failed: {exc}"
    if not description:
        return "Error: vision model returned an empty description"
    return (f"[Note: image {note}]\n\n" if note else "") + description


__all__ = ["run"]
