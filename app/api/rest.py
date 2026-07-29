"""JSON REST API — dashboard, agents, sessions, chat history."""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
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
    SessionChatIn,
    SessionCreate,
    SessionWorkplaceIn,
)
from app.services import store

router = APIRouter(prefix="/api")


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


@router.get("/sessions/{session_id}")
async def get_session_api(session_id: str, _: AuthDep):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    agents = store.list_agents()
    agent_map = {a["id"]: a for a in agents}
    ids = session.get("agent_ids") or ([session["agent_id"]] if session.get("agent_id") else [])
    is_swarm = bool(session.get("is_swarm")) or len(ids) > 1
    return {
        **session,
        "agent_ids": ids,
        "agents": [agent_map[a] for a in ids if a in agent_map],
        "is_swarm": is_swarm,
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
    """Start a coordinator-only chat from the dashboard home composer.

    No agent picker — always routes to the swarm coordinator (``is_super``).
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
    return {"entries": entries}


@router.get("/sessions/{session_id}/attachments")
async def list_session_attachments_api(session_id: str, _: AuthDep):
    session = store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"attachments": store.list_session_attachments(session_id)}


@router.post("/sessions/{session_id}/attachments")
async def upload_session_attachment(
    session_id: str,
    request: Request,
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
    safe_name = Path((name or file.filename or "upload")).name[:120] or "upload"
    attachment_id = f"att_{uuid4().hex[:18]}"
    storage_dir = Path(TOMO_HOME) / "attachments" / session_id
    storage_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(safe_name).suffix or (Path(file.filename or "").suffix or ".bin")
    stored_name = f"{attachment_id}{ext}"
    stored_path = storage_dir / stored_name
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
    except Exception:
        pass
    store.delete_attachment(attachment_id)
    return {"success": True}


@router.get("/sessions/{session_id}/context")
async def session_context_usage(session_id: str, _: AuthDep):
    from app.runtime.agent.context_usage import compute_context_usage

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
    return compute_context_usage(agent_id, history)


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

    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    user_id = _uid(request, request.query_params.get("user_id"))
    history = store.get_history(agent_id, user_id)
    return compute_context_usage(agent_id, history)


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
