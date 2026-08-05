"""JSON REST API — dashboard, agents, sessions, chat history."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.config import TOMO_HOME
from app.core.deps import AuthDep, session_user_id
from app.schemas import (
    AgentCreate,
    AgentDraft,
    AgentGenerateIn,
    AgentUpdate,
    ChatMessageIn,
    HomeSessionIn,
    SessionCreate,
    SessionWorkplaceIn,
)
from app.services import store

router = APIRouter(prefix="/api")

_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _uid(request: Request, explicit: str | None = None) -> str:
    uid = (explicit or "").strip()
    if uid and uid != "web":
        return uid
    return session_user_id(request)

@router.get("/dashboard/data")
async def dashboard_data(_: AuthDep):
    data = store.dashboard_data()
    coord = store.get_coordinator()
    data["coordinator"] = (
        {"id": coord["id"], "name": coord["name"]} if coord else None
    )
    return data


@router.get("/companion")
async def companion_snapshot_api(_: AuthDep):
    """Bond, growth ledger, profile preview for the Companion page."""
    return store.companion_snapshot()


@router.get("/companion/events")
async def companion_events_api(
    _: AuthDep,
    limit: int = Query(30, ge=1, le=200),
    before: float | None = Query(None),
    agent_id: str | None = Query(None),
    saved_only: bool = Query(False),
):
    """Paginated growth log (learning events)."""
    events = store.list_learning_events(
        limit=limit, before=before, agent_id=agent_id, saved_only=saved_only
    )
    next_before = None
    if events and len(events) >= limit:
        next_before = float(events[-1].get("created_at") or 0) or None
    return {"events": events, "next_before": next_before}


@router.get("/dashboard/sidebar")
async def dashboard_sidebar(_: AuthDep):
    return {"agents": store.list_agents()}


@router.get("/agents")
async def list_agents(_: AuthDep):
    return {"agents": store.list_agents()}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, _: AuthDep):
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("/agents/generate", response_model=AgentDraft)
async def generate_agent(body: AgentGenerateIn, _: AuthDep):
    from app.runtime.agent_generate import generate_agent_draft
    from app.runtime.llm import LLMConfigError

    try:
        draft = await generate_agent_draft(
            body.brief,
            existing_agents=store.list_agents(),
        )
    except LLMConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))
    if not draft:
        raise HTTPException(
            status_code=502,
            detail="Could not generate agent from brief. Try again or use Advanced.",
        )
    return AgentDraft(**draft)


@router.post("/agents")
async def create_agent(body: AgentCreate, _: AuthDep):
    try:
        return store.create_agent(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate, _: AuthDep):
    try:
        agent = store.update_agent(agent_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, _: AuthDep):
    if not store.delete_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"success": True}


@router.get("/sessions")
async def list_sessions_api(_: AuthDep):
    agents = store.list_agents()
    agent_map = {a["id"]: a for a in agents}
    sessions = []
    for s in store.list_sessions():
        row = dict(s)
        ids = row.get("agent_ids") or ([row["agent_id"]] if row.get("agent_id") else [])
        row["agent_ids"] = ids
        row["coordinator_id"] = row.get("coordinator_id") or row.get("agent_id")
        names = [agent_map[a]["name"] for a in ids if a in agent_map]
        row["agent_names"] = names
        # Do not expose a countable roster in labels — swarm is open-ended.
        is_swarm = bool(row.get("is_swarm")) or len(ids) > 1
        row["is_swarm"] = is_swarm
        row["agent_name"] = "swarm" if is_swarm else (names[0] if names else row.get("agent_id", ""))
        sessions.append(row)
    return {"sessions": sessions, "agents": agents}


@router.get("/sessions/search")
async def search_sessions_api(
    _: AuthDep,
    q: str = Query(default="", description="Search chat titles and message content"),
    limit: int = Query(default=40, ge=1, le=100),
):
    """Search sessions by title and message content (Gemini-style chat search)."""
    text = (q or "").strip()
    if not text:
        return {"query": "", "results": []}

    sessions = {s["id"]: s for s in store.list_sessions()}
    seen: dict[str, dict] = {}
    needle = text.lower()

    for s in sessions.values():
        title = (s.get("title") or "").strip()
        if needle in title.lower() or needle in (s.get("id") or "").lower():
            seen[s["id"]] = {
                "session_id": s["id"],
                "title": title or "Conversation",
                "snippet": (str(s.get("message_count") or 0) + " msgs"),
                "updated_at": s.get("updated_at") or 0,
                "match": "title",
            }

    try:
        hits = store.search_messages(text, limit=limit)
    except Exception:
        hits = []

    for hit in hits:
        sid = hit.get("session_id") or ""
        s = sessions.get(sid)
        if not s:
            continue
        content = (hit.get("content") or "").strip().replace("\n", " ")
        if len(content) > 140:
            content = content[:140].rstrip() + "…"
        prev = seen.get(sid)
        if not prev or prev.get("match") == "title":
            seen[sid] = {
                "session_id": sid,
                "title": (s.get("title") or "").strip() or "Conversation",
                "snippet": content or (str(s.get("message_count") or 0) + " msgs"),
                "updated_at": s.get("updated_at") or hit.get("ts") or 0,
                "match": "message",
            }

    results = sorted(seen.values(), key=lambda r: -(r.get("updated_at") or 0))[:limit]
    return {"query": text, "results": results}


@router.get("/sessions/{session_id}")
async def get_session_api(session_id: str, _: AuthDep):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    agents = store.list_agents()
    agent_map = {a["id"]: a for a in agents}
    ids = session.get("agent_ids") or ([session["agent_id"]] if session.get("agent_id") else [])
    is_swarm = bool(session.get("is_swarm")) or len(ids) > 1
    from app.runtime.permissions.modes import mode_payload

    return {
        **session,
        "agent_ids": ids,
        "agents": [agent_map[a] for a in ids if a in agent_map],
        "is_swarm": is_swarm,
        "approval": mode_payload(session_id),
    }


@router.post("/sessions")
async def create_session(body: SessionCreate, request: Request, _: AuthDep):
    try:
        session_id = store.create_swarm_session(
            body.agent_ids,
            _uid(request, body.user_id),
            body.coordinator_id,
            workplace_id=body.workplace_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"session_id": session_id, "workplace_id": (body.workplace_id or "")}


@router.put("/sessions/{session_id}/workplace")
async def set_session_workplace_api(session_id: str, body: SessionWorkplaceIn, _: AuthDep):
    """Set or clear this chat's default workplace (prefer local for folder context)."""
    try:
        session = store.set_session_workplace(session_id, body.workplace_id or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/sessions/{session_id}")
async def delete_session_api(session_id: str, _: AuthDep):
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": True}


@router.post("/sessions/prune-drafts")
async def prune_draft_sessions(_: AuthDep, keep_id: str | None = None):
    """Delete never-messaged draft sessions (default title + zero messages)."""
    deleted = store.prune_empty_draft_sessions(keep_id=keep_id or None)
    return {"deleted": deleted}


@router.post("/sessions/home")
async def create_home_session(body: HomeSessionIn, request: Request, _: AuthDep):
    """Start a full-swarm chat from the dashboard home composer.

    No agent picker — members are all enabled agents so ``delegate`` works
    immediately. Coordinator is the super agent (``is_super``).
    Optional ``message`` is returned so the client can deep-link
    ``/sessions?s=<id>&q=...`` and auto-send once.
    """
    try:
        created = store.create_home_session(_uid(request, body.user_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**created, "message": (body.message or "").strip()}


@router.put("/sessions/{session_id}/agents")
async def update_session_agents(session_id: str, body: SessionCreate, _: AuthDep):
    try:
        session = store.update_session_agents(session_id, body.agent_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/sessions/{session_id}/chat")
async def session_chat_history(session_id: str, _: AuthDep):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    entries = store.get_session_history(session_id)
    return {"entries": entries, "has_more": False, "session": session}


@router.get("/sessions/{session_id}/attachments")
async def list_session_attachments_api(session_id: str, _: AuthDep):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"attachments": store.list_session_attachments(session_id)}


@router.post("/sessions/{session_id}/attachments")
async def upload_session_attachment(
    session_id: str,
    _: AuthDep,
    file: UploadFile = File(...),
    name: str | None = Form(None),
):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 20MB)")
    safe_name = Path((name or file.filename or "upload")).name[:120] or "upload"
    attachment_id = f"att_{uuid4().hex[:18]}"
    from app.core.paths import ensure_under

    try:
        att_root = (Path(TOMO_HOME) / "attachments").resolve()
        att_root.mkdir(parents=True, exist_ok=True)
        storage_dir = ensure_under(att_root, session_id)
        storage_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(safe_name).suffix or (Path(file.filename or "").suffix or ".bin")
        stored_name = f"{attachment_id}{ext}"
        stored_path = ensure_under(storage_dir, stored_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stored_path.write_bytes(data)
    mime = file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    attachment = store.create_attachment(
        attachment_id=attachment_id,
        session_id=session_id,
        filename=stored_name,
        original_name=safe_name,
        mime_type=mime,
        size_bytes=len(data),
        file_path=str(stored_path),
    )
    return attachment


@router.get("/attachments/{attachment_id}")
async def download_attachment(attachment_id: str, _: AuthDep):
    att = store.get_attachment(attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = Path(att["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        filename=att["original_name"] or att["filename"],
        media_type=att["mime_type"] or "application/octet-stream",
    )


@router.delete("/attachments/{attachment_id}")
async def delete_attachment_api(attachment_id: str, _: AuthDep):
    att = store.get_attachment(attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    try:
        Path(att["file_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    store.delete_attachment(attachment_id)
    return {"success": True}


@router.get("/sessions/{session_id}/context")
async def session_context_usage(session_id: str, _: AuthDep):
    from app.runtime.agent.context_usage import compute_context_usage
    from app.runtime.llm.context_window import resolve_context_window

    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    agent_id = (
        session.get("coordinator_id")
        or session.get("agent_id")
        or (session.get("agent_ids") or [None])[0]
    )
    if not agent_id:
        raise HTTPException(status_code=400, detail="Session has no coordinator")
    history = store.get_session_history(session_id)
    limit = await resolve_context_window(agent_id)
    return compute_context_usage(agent_id, history, limit=limit)


@router.post("/sessions/{session_id}/chat/clear")
async def session_chat_clear(session_id: str, _: AuthDep):
    if not store.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    store.clear_session_by_id(session_id)
    return {"success": True}


@router.get("/agents/{agent_id}/chat")
async def chat_history(agent_id: str, request: Request, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    user_id = _uid(request, request.query_params.get("user_id"))
    entries = store.get_history(agent_id, user_id)
    return {"entries": entries, "has_more": False}


@router.get("/agents/{agent_id}/context")
async def agent_context_usage(agent_id: str, request: Request, _: AuthDep):
    from app.runtime.agent.context_usage import compute_context_usage
    from app.runtime.llm.context_window import resolve_context_window

    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    user_id = _uid(request, request.query_params.get("user_id"))
    history = store.get_history(agent_id, user_id)
    limit = await resolve_context_window(agent_id)
    return compute_context_usage(agent_id, history, limit=limit)


@router.post("/agents/{agent_id}/chat")
async def chat_send(agent_id: str, body: ChatMessageIn, request: Request, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    session_id = store.get_or_create_session(agent_id, _uid(request, body.user_id))
    return {"success": True, "session_id": session_id, "streaming": True}


@router.post("/agents/{agent_id}/chat/clear")
async def chat_clear(agent_id: str, request: Request, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    user_id = _uid(request, request.query_params.get("user_id"))
    store.clear_session(agent_id, user_id)
    return {"success": True}


# ── Session artifacts ($TOMO_HOME/sessions/<id>/artifacts/) — Kimi-style ──


def _require_session(session_id: str) -> dict:
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/sessions/{session_id}/artifacts")
async def list_session_artifacts(
    session_id: str,
    _: AuthDep,
    sort: str = Query("newest"),
    q: str = Query(""),
    type: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=200),
):
    _require_session(session_id)
    from app.runtime.artifacts.fs import list_artifact_files

    return list_artifact_files(
        session_id,
        filter=q,
        type=type,
        sort=sort,
        page=page,
        limit=limit,
    )


@router.get("/sessions/{session_id}/artifacts/{filename}")
async def get_session_artifact(session_id: str, filename: str, _: AuthDep):
    _require_session(session_id)
    from app.runtime.artifacts.fs import artifacts_dir, validate_filename

    err = validate_filename(filename)
    if err:
        raise HTTPException(status_code=400, detail=err)
    base = artifacts_dir(session_id).resolve()
    path = (base / filename).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    headers: dict[str, str] = {"X-Content-Type-Options": "nosniff"}
    # Never serve agent-authored HTML as an active document on Tomo origin.
    # UI previews load via sandboxed srcdoc instead.
    lower = filename.lower()
    if lower.endswith((".html", ".htm")):
        return FileResponse(
            path,
            media_type="text/plain; charset=utf-8",
            filename=filename,
            content_disposition_type="attachment",
            headers=headers,
        )
    return FileResponse(
        path,
        media_type=mime,
        filename=filename,
        content_disposition_type="inline",
        headers=headers,
    )


@router.delete("/sessions/{session_id}/artifacts/{filename}")
async def delete_session_artifact(session_id: str, filename: str, _: AuthDep):
    _require_session(session_id)
    from app.runtime.artifacts.fs import delete_artifact_file, validate_filename

    err = validate_filename(filename)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not delete_artifact_file(session_id, filename):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"success": True}


@router.post("/sessions/{session_id}/artifacts")
async def create_session_artifact(session_id: str, body: dict, _: AuthDep):
    """Create a text artifact: ``{filename, content}``."""
    session = _require_session(session_id)
    from app.runtime.artifacts.fs import validate_filename, write_artifact_text

    filename = str((body or {}).get("filename") or "").strip()
    content = (body or {}).get("content")
    err = validate_filename(filename)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")
    info = write_artifact_text(session_id, filename, content)
    agent_id = ""
    ids = session.get("agent_ids") or []
    if ids:
        agent_id = str(ids[0])
    elif session.get("coordinator_id"):
        agent_id = str(session["coordinator_id"])
    try:
        store.create_artifact(
            {
                "title": filename,
                "path": info["filepath"],
                "kind": "export",
                "session_id": session_id,
                "agent_id": agent_id,
            }
        )
    except Exception:
        pass
    return info


# Compat: agent routes require ?session_id= (artifacts are session-scoped).
@router.get("/agents/{agent_id}/artifacts")
async def list_agent_artifacts_compat(
    agent_id: str,
    _: AuthDep,
    session_id: str = Query(..., min_length=1),
    sort: str = Query("newest"),
    q: str = Query(""),
    type: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=200),
):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    _require_session(session_id)
    from app.runtime.artifacts.fs import list_artifact_files

    return list_artifact_files(
        session_id,
        filter=q,
        type=type,
        sort=sort,
        page=page,
        limit=limit,
    )


def _serve_artifact_file(session_id: str, filename: str, *, download: bool) -> FileResponse:
    """Serve an artifact file with the same security rules as the auth endpoint."""
    from app.runtime.artifacts.fs import artifacts_dir, validate_filename

    err = validate_filename(filename)
    if err:
        raise HTTPException(status_code=400, detail=err)
    base = artifacts_dir(session_id).resolve()
    path = (base / filename).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    headers: dict[str, str] = {"X-Content-Type-Options": "nosniff"}
    lower = filename.lower()
    if lower.endswith((".html", ".htm")):
        return FileResponse(
            path,
            media_type="text/plain; charset=utf-8",
            filename=filename,
            content_disposition_type="attachment",
            headers=headers,
        )
    return FileResponse(
        path,
        media_type=mime,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
        headers=headers,
    )


@router.post("/sessions/{session_id}/artifacts/{filename}/share")
async def share_session_artifact(
    session_id: str, filename: str, request: Request, _: AuthDep
):
    """Create or return the existing public share link for an artifact."""
    _require_session(session_id)
    from app.runtime.artifacts.fs import artifacts_dir, validate_filename

    err = validate_filename(filename)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not (artifacts_dir(session_id) / filename).is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    share = store.share_artifact(
        session_id, filename, created_by=session_user_id(request)
    )
    return {"token": share["token"], "share_url": f"/share/{share['token']}"}


@router.get("/sessions/{session_id}/artifacts/{filename}/share")
async def get_session_artifact_share(session_id: str, filename: str, _: AuthDep):
    """Check whether an artifact already has a public share link."""
    _require_session(session_id)
    from app.runtime.artifacts.fs import validate_filename

    err = validate_filename(filename)
    if err:
        raise HTTPException(status_code=400, detail=err)
    share = store.get_artifact_share_by_file(session_id, filename)
    if not share:
        return {"shared": False}
    return {
        "shared": True,
        "token": share["token"],
        "share_url": f"/share/{share['token']}",
    }


@router.delete("/sessions/{session_id}/artifacts/{filename}/share")
async def revoke_session_artifact_share(session_id: str, filename: str, _: AuthDep):
    """Revoke the public share link for an artifact."""
    _require_session(session_id)
    from app.runtime.artifacts.fs import validate_filename

    err = validate_filename(filename)
    if err:
        raise HTTPException(status_code=400, detail=err)
    store.revoke_artifact_share(session_id, filename)
    return {"success": True}


@router.get("/share/{token}/raw")
async def get_shared_artifact_raw(token: str):
    """Public raw access to a shared artifact (HTML is forced to text/plain)."""
    share = store.get_artifact_share(token)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    return _serve_artifact_file(share["session_id"], share["filename"], download=False)


@router.get("/share/{token}/download")
async def get_shared_artifact_download(token: str):
    """Public download access to a shared artifact."""
    share = store.get_artifact_share(token)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    return _serve_artifact_file(share["session_id"], share["filename"], download=True)
