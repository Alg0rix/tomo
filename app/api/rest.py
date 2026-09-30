"""JSON REST API — dashboard, agents, sessions, chat history."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.config import TOMO_HOME
from app.core.deps import AuthDep, require_owned_session, session_user_id
from app.schemas import (
    AgentCreate,
    AgentDraft,
    AgentGenerateIn,
    AgentUpdate,
    ChatMessageIn,
    HomeSessionIn,
    ReasoningEffortUpdate,
    SessionCreate,
    SessionWorkplaceIn,
)
from app.services import store

router = APIRouter(prefix="/api")

_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _uid(request: Request, explicit: str | None = None) -> str:
    """Authenticated account id only — never trust client-supplied user_id."""
    _ = explicit  # retained for call-site compatibility
    return session_user_id(request)


@router.get("/dashboard/prompts")
async def dashboard_prompts_api(request: Request, _: AuthDep):
    """Dynamic 'Try asking' chip prompts, personalized per account."""
    from app.runtime import dashboard_prompts

    return await dashboard_prompts.get_dashboard_prompts(session_user_id(request))


@router.get("/dashboard/data")
async def dashboard_data(request: Request, _: AuthDep):
    data = store.dashboard_data(user_id=session_user_id(request))
    coord = store.get_coordinator()
    data["coordinator"] = (
        {"id": coord["id"], "name": coord["name"]} if coord else None
    )
    return data


@router.get("/companion")
async def companion_snapshot_api(request: Request, _: AuthDep):
    """Bond, growth ledger, profile preview for the Companion page (per login)."""
    return store.companion_snapshot(user_id=session_user_id(request))


@router.get("/episodes")
async def list_episodes_api(
    request: Request,
    _: AuthDep,
    q: str | None = Query(None, max_length=500),
    limit: int = Query(20, ge=1, le=100),
    state: str | None = Query("active"),
):
    """List or search this account's episodic experiences."""
    uid = session_user_id(request)
    query = (q or "").strip()
    if query:
        return {"episodes": store.search_episodes(query, user_id=uid, limit=limit)}
    return {
        "episodes": store.list_episodes(user_id=uid, state=state, limit=limit)
    }


@router.post("/episodes")
async def create_episode_api(request: Request, body: dict, _: AuthDep):
    """Record a structured episodic experience for the logged-in user."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    data = dict(body)
    data["user_id"] = session_user_id(request)
    ep = store.insert_episode(data)
    if not ep:
        raise HTTPException(status_code=400, detail="Could not record episode")
    return ep


# Static path segments must be registered before /episodes/{episode_id}.
@router.get("/episodes/meta/contradictions")
async def episode_contradictions_api(request: Request, _: AuthDep):
    """Conflicting outcomes for similar objectives (same account)."""
    return {
        "contradictions": store.episode_contradictions(
            user_id=session_user_id(request), limit=30
        )
    }


@router.post("/episodes/meta/optimize")
async def episode_optimize_api(request: Request, _: AuthDep):
    """LTM pass: decay, semantic consolidation, procedure extraction."""
    return store.optimize_episodic_ltm(user_id=session_user_id(request))


@router.post("/episodes/session/{session_id}/open")
async def episode_open_api(
    session_id: str, request: Request, body: dict, _: AuthDep
):
    """Start an open episode boundary for a chat session."""
    from app.runtime.memory.episodes import open_episode

    require_owned_session(request, session_id)
    data = body if isinstance(body, dict) else {}
    ep = open_episode(
        session_id=session_id,
        user_id=session_user_id(request),
        agent_id=str(data.get("agent_id") or ""),
        objective=str(data.get("objective") or data.get("message") or ""),
        context_summary=str(data.get("context_summary") or ""),
        workplace_id=str(data.get("workplace_id") or ""),
    )
    if not ep:
        raise HTTPException(status_code=400, detail="Could not open episode")
    return ep


@router.post("/episodes/session/{session_id}/close")
async def episode_close_api(
    session_id: str, request: Request, body: dict, _: AuthDep
):
    """Close the open episode for a chat session."""
    from app.runtime.memory.episodes import close_episode

    require_owned_session(request, session_id)
    data = body if isinstance(body, dict) else {}
    ep = close_episode(
        session_id,
        outcome_status=str(data.get("outcome_status") or "success"),
        outcome_summary=str(data.get("outcome_summary") or ""),
        reflection=str(data.get("reflection") or ""),
    )
    return {"ok": True, "episode": ep}


@router.get("/episodes/{episode_id}")
async def get_episode_api(episode_id: str, request: Request, _: AuthDep):
    from app.models.mixins import episodic as ep_mod

    uid = session_user_id(request)
    with store._lock:
        ep = ep_mod.get_episode(
            store._conn, episode_id, user_id=uid, touch=True
        )
        if ep:
            ep = dict(ep)
            ep["events"] = ep_mod.list_events(store._conn, episode_id)
            ep["related"] = ep_mod.graph_neighbors(store._conn, [episode_id], limit=8)
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")
    return ep


@router.post("/episodes/{episode_id}/feedback")
async def episode_feedback_api(
    episode_id: str, request: Request, body: dict, _: AuthDep
):
    """Mark whether a retrieved episode was helpful for a later decision."""
    helpful = True
    if isinstance(body, dict) and "helpful" in body:
        helpful = bool(body.get("helpful"))
    ok = store.episode_feedback(
        episode_id, helpful=helpful, user_id=session_user_id(request)
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"ok": True, "episode_id": episode_id, "helpful": helpful}


@router.get("/companion/events")
async def companion_events_api(
    request: Request,
    _: AuthDep,
    limit: int = Query(30, ge=1, le=200),
    before: float | None = Query(None),
    agent_id: str | None = Query(None),
    saved_only: bool = Query(False),
):
    """Paginated growth log (learning events for this account only)."""
    events = store.list_learning_events(
        limit=limit,
        before=before,
        agent_id=agent_id,
        saved_only=saved_only,
        user_id=session_user_id(request),
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
async def list_sessions_api(request: Request, _: AuthDep):
    agents = store.list_agents()
    agent_map = {a["id"]: a for a in agents}
    uid = session_user_id(request)
    sessions = []
    for s in store.list_sessions(user_id=uid):
        row = dict(s)
        ids = row.get("agent_ids") or ([row["agent_id"]] if row.get("agent_id") else [])
        row["agent_ids"] = ids
        row["coordinator_id"] = row.get("coordinator_id") or row.get("agent_id")
        names = [agent_map[a]["name"] for a in ids if a in agent_map]
        row["agent_names"] = names
        # Do not expose a countable roster in labels — swarm is open-ended.
        is_swarm = bool(row.get("is_swarm")) or len(ids) > 1
        row["is_swarm"] = is_swarm
        row["agent_name"] = names[0] if names else row.get("agent_id", "")
        sessions.append(row)
    return {"sessions": sessions, "agents": agents}


@router.get("/sessions/search")
async def search_sessions_api(
    request: Request,
    _: AuthDep,
    q: str = Query(default="", description="Search chat titles and message content"),
    limit: int = Query(default=40, ge=1, le=100),
):
    """Search sessions by title and message content (Gemini-style chat search)."""
    text = (q or "").strip()
    if not text:
        return {"query": "", "results": []}

    uid = session_user_id(request)
    sessions = {s["id"]: s for s in store.list_sessions(user_id=uid)}
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
async def get_session_api(session_id: str, request: Request, _: AuthDep):
    session = require_owned_session(request, session_id)
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


@router.get("/sessions/{session_id}/reasoning-effort")
async def get_session_reasoning_effort_api(
    session_id: str, request: Request, _: AuthDep
):
    require_owned_session(request, session_id)
    state = store.get_session_reasoning_effort(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return state


@router.put("/sessions/{session_id}/reasoning-effort")
async def set_session_reasoning_effort_api(
    session_id: str,
    body: ReasoningEffortUpdate,
    request: Request,
    _: AuthDep,
):
    require_owned_session(request, session_id)
    try:
        state = store.set_session_reasoning_effort(
            session_id, body.reasoning_effort
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return state


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
async def set_session_workplace_api(
    session_id: str, body: SessionWorkplaceIn, request: Request, _: AuthDep
):
    """Set or clear this chat's default workplace (prefer local for folder context)."""
    require_owned_session(request, session_id)
    try:
        session = store.set_session_workplace(session_id, body.workplace_id or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.delete("/sessions/{session_id}")
async def delete_session_api(session_id: str, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
    if not store.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": True}


@router.post("/sessions/prune-drafts")
async def prune_draft_sessions(
    request: Request, _: AuthDep, keep_id: str | None = None
):
    """Delete never-messaged draft sessions (default title + zero messages)."""
    deleted = store.prune_empty_draft_sessions(
        keep_id=keep_id or None, user_id=session_user_id(request)
    )
    return {"deleted": deleted}


@router.post("/sessions/home")
async def create_home_session(body: HomeSessionIn, request: Request, _: AuthDep):
    """Start a solo chat from the dashboard home composer.

    Coordinator is the super agent (``is_super``). A later chat turn can
    explicitly request or approve a team.
    Optional ``message`` is returned so the client can deep-link
    ``/sessions?s=<id>&q=...`` and auto-send once.
    """
    try:
        created = store.create_home_session(_uid(request, body.user_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**created, "message": (body.message or "").strip()}


@router.put("/sessions/{session_id}/agents")
async def update_session_agents(
    session_id: str, body: SessionCreate, request: Request, _: AuthDep
):
    require_owned_session(request, session_id)
    try:
        session = store.update_session_agents(session_id, body.agent_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/sessions/{session_id}/chat")
async def session_chat_history(session_id: str, request: Request, _: AuthDep):
    session = require_owned_session(request, session_id)
    entries = store.get_session_history(session_id)
    return {"entries": entries, "has_more": False, "session": session}


@router.get("/sessions/{session_id}/swarm")
async def session_swarm_history(session_id: str, request: Request, _: AuthDep):
    """Durable task state for reload and inspection; owner-scoped."""
    require_owned_session(request, session_id)
    from app.models.mixins import swarm as swarm_store

    def read(conn):
        runs = swarm_store.list_runs(conn, session_id)
        return {
            "agents": swarm_store.list_agents(conn, session_id),
            "runs": [{**run,
                      "tasks": swarm_store.list_tasks(conn, run["id"]),
                      "events": swarm_store.list_events(conn, run["id"])}
                     for run in runs[:20]],
        }

    return store.with_db(read)


@router.get("/sessions/{session_id}/attachments")
async def list_session_attachments_api(session_id: str, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
    return {"attachments": store.list_session_attachments(session_id)}


@router.post("/sessions/{session_id}/attachments")
async def upload_session_attachment(
    session_id: str,
    request: Request,
    _: AuthDep,
    file: UploadFile = File(...),
    name: str | None = Form(None),
):
    require_owned_session(request, session_id)
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
async def download_attachment(attachment_id: str, request: Request, _: AuthDep):
    att = store.get_attachment(attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    sid = (att.get("session_id") or "").strip()
    if sid:
        require_owned_session(request, sid)
    path = Path(att["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        filename=att["original_name"] or att["filename"],
        media_type=att["mime_type"] or "application/octet-stream",
    )


@router.delete("/attachments/{attachment_id}")
async def delete_attachment_api(attachment_id: str, request: Request, _: AuthDep):
    att = store.get_attachment(attachment_id)
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    sid = (att.get("session_id") or "").strip()
    if sid:
        require_owned_session(request, sid)
    try:
        Path(att["file_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    store.delete_attachment(attachment_id)
    return {"success": True}


@router.get("/sessions/{session_id}/context")
async def session_context_usage(session_id: str, request: Request, _: AuthDep):
    from app.runtime.agent.context_usage import compute_context_usage
    from app.runtime.llm.context_window import resolve_context_window

    session = require_owned_session(request, session_id)
    agent_id = (
        session.get("coordinator_id")
        or session.get("agent_id")
        or (session.get("agent_ids") or [None])[0]
    )
    if not agent_id:
        raise HTTPException(status_code=400, detail="Session has no coordinator")
    history = store.get_session_history(session_id)
    limit = await resolve_context_window(agent_id)
    from app.runtime.agent.metrics import session_usage

    return {**compute_context_usage(agent_id, history, limit=limit), "usage": session_usage(history)}


@router.post("/sessions/{session_id}/chat/clear")
async def session_chat_clear(session_id: str, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
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
    from app.runtime.agent.metrics import session_usage

    return {**compute_context_usage(agent_id, history, limit=limit), "usage": session_usage(history)}


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


def _require_session(request: Request, session_id: str) -> dict:
    return require_owned_session(request, session_id)


@router.get("/sessions/{session_id}/artifacts")
async def list_session_artifacts(
    session_id: str,
    request: Request,
    _: AuthDep,
    sort: str = Query("newest"),
    q: str = Query(""),
    type: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=200),
):
    _require_session(request, session_id)
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
async def get_session_artifact(
    session_id: str, filename: str, request: Request, _: AuthDep
):
    _require_session(request, session_id)
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
    # UI previews load on an isolated data origin inside a sandboxed iframe.
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
async def delete_session_artifact(
    session_id: str, filename: str, request: Request, _: AuthDep
):
    _require_session(request, session_id)
    from app.runtime.artifacts.fs import delete_artifact_file, validate_filename

    err = validate_filename(filename)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not delete_artifact_file(session_id, filename):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"success": True}


@router.post("/sessions/{session_id}/artifacts")
async def create_session_artifact(
    session_id: str, body: dict, request: Request, _: AuthDep
):
    """Create a text artifact: ``{filename, content}``."""
    session = _require_session(request, session_id)
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
    request: Request,
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
    _require_session(request, session_id)
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
    _require_session(request, session_id)
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
async def get_session_artifact_share(
    session_id: str, filename: str, request: Request, _: AuthDep
):
    """Check whether an artifact already has a public share link."""
    _require_session(request, session_id)
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
async def revoke_session_artifact_share(
    session_id: str, filename: str, request: Request, _: AuthDep
):
    """Revoke the public share link for an artifact."""
    _require_session(request, session_id)
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

# Markdown vault API — every query is bound to the authenticated account.
@router.get('/memory/graph')
async def memory_graph_api(request: Request, _: AuthDep, until: str | None = None):
    from datetime import date
    from app.runtime.memory.vault import index
    from app.runtime.memory.vault.paths import TYPES
    uid = session_user_id(request)
    if until:
        try:
            until = date.fromisoformat(until).isoformat()
        except ValueError:
            raise HTTPException(400, 'Invalid date')
    def query(conn):
        index.rebuild(conn, uid)
        rows = conn.execute('SELECT * FROM vault_docs WHERE user_id=? AND kind="entity" ORDER BY type,slug', (uid,)).fetchall()
        entities = [dict(r) for r in rows if not until or not r['updated'] or r['updated'] <= until]
        keys = {r['path'] for r in entities}
        edges = [dict(r) for r in conn.execute('SELECT l.* FROM vault_links l JOIN vault_docs d ON d.path=l.src WHERE d.user_id=?', (uid,)).fetchall() if r['src'] in keys and r['dst_resolved'] in keys]
        backlinks = {r['dst_resolved']: 0 for r in edges}
        for edge in edges:
            backlinks[edge['dst_resolved']] += 1
        nodes = [{'id': r['path'], 'type': r['type'], 'slug': r['slug'], 'title': r['title'], 'facts': len([x for x in r['body'].splitlines() if x.startswith('§')]), 'backlinks': backlinks.get(r['path'], 0), 'updated': r['updated']} for r in entities]
        days = [r['slug'] for r in conn.execute('SELECT slug FROM vault_docs WHERE user_id=? AND kind="timeline" ORDER BY slug', (uid,)).fetchall()]
        return {'nodes': nodes, 'edges': edges, 'days': days, 'types': sorted(TYPES) if entities else []}
    return store.with_db(query)


@router.get('/memory/entity/{entity_type}/{slug}')
async def memory_entity_api(request: Request, entity_type: str, slug: str, _: AuthDep):
    from app.runtime.memory.vault import doc, paths
    uid = session_user_id(request)
    try:
        path = paths.entity_path(uid, f'{entity_type}/{slug}')
    except ValueError:
        raise HTTPException(404, 'Entity not found')
    if not path.is_file():
        raise HTTPException(404, 'Entity not found')
    raw = path.read_text(encoding='utf-8')
    page = doc.parse(raw)
    return {'entity': f'{entity_type}/{slug}', 'raw': raw, 'title': next((s[2:] for s in page.body.splitlines() if s.startswith('# ')), slug), 'facts': page.entries, 'meta': page.meta}


@router.post('/memory/entity/{entity_type}/{slug}/forget')
async def memory_forget_api(request: Request, entity_type: str, slug: str, body: dict, _: AuthDep):
    from app.runtime.memory.vault.write import forget_fact
    if type(body.get('number')) is not int:
        raise HTTPException(400, 'Fact number required')
    try:
        changed = forget_fact(session_user_id(request), f'{entity_type}/{slug}', body['number'])
    except ValueError:
        raise HTTPException(404, 'Entity not found')
    if not changed:
        raise HTTPException(404, 'Fact not found')
    return {'ok': True}


@router.post('/memory/entity/{entity_type}/{slug}/edit')
async def memory_edit_api(request: Request, entity_type: str, slug: str, body: dict, _: AuthDep):
    return _correct_memory(request, entity_type, slug, body, move=False)


@router.post('/memory/entity/{entity_type}/{slug}/move')
async def memory_move_api(request: Request, entity_type: str, slug: str, body: dict, _: AuthDep):
    return _correct_memory(request, entity_type, slug, body, move=True)


def _correct_memory(request: Request, entity_type: str, slug: str, body: dict, *, move: bool):
    from app.runtime.memory.vault.write import correct_fact

    if type(body.get('number')) is not int:
        raise HTTPException(400, 'Fact number required')
    field = 'destination' if move else 'text'
    if not isinstance(body.get(field), str) or not body[field].strip():
        raise HTTPException(400, f'{field} required')
    if 'expected' in body and not isinstance(body['expected'], str):
        raise HTTPException(400, 'Invalid expected fact')
    try:
        changed = correct_fact(session_user_id(request), f'{entity_type}/{slug}', body['number'],
                               expected=body.get('expected'), **{field: body[field]})
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not changed:
        raise HTTPException(409, 'Fact changed or no longer exists; refresh and retry')
    return {'ok': True}


def _memory_resolver(conn, uid: str) -> dict[str, str]:
    """Map every way a page can be linked (key, slug, alias) to its type/slug key."""
    ents = conn.execute('SELECT path,type,slug FROM vault_docs WHERE user_id=? AND kind="entity"', (uid,)).fetchall()
    by_path = {r['path']: f"{r['type']}/{r['slug']}" for r in ents}
    names: dict[str, str] = {}
    for r in conn.execute('SELECT alias,path FROM vault_aliases WHERE path IN (SELECT path FROM vault_docs WHERE user_id=?) ORDER BY path', (uid,)).fetchall():
        names.setdefault(r['alias'].casefold(), by_path.get(r['path'], ''))
    names.update({key.casefold(): key for key in by_path.values()})
    return {k: v for k, v in names.items() if v}


def _journal_keys(entry: dict, resolver: dict[str, str]) -> list[str]:
    keys = (resolver.get(link.split('#')[0].strip().casefold()) for link in entry['links'])
    return list(dict.fromkeys(k for k in keys if k))


@router.get('/memory/overview')
async def memory_overview_api(request: Request, _: AuthDep):
    """Pages with their facts, the links between them, and a per-day activity
    count for the journal heatmap. Journal entries themselves are paged
    through /memory/journal so a long history never loads at once."""
    from app.runtime.memory.vault import doc, index, journal
    uid = session_user_id(request)

    def fact_rows(body):
        return [dict(n=i, **doc.fact_data(entry)) for i, entry in enumerate(doc.parse(body).entries)]

    def query(conn):
        index.rebuild(conn, uid)
        ents = conn.execute('SELECT path,type,slug,title,updated,tags,body FROM vault_docs WHERE user_id=? AND kind="entity" ORDER BY type,slug', (uid,)).fetchall()
        paths = {r['path']: f"{r['type']}/{r['slug']}" for r in ents}
        aliases: dict[str, list[str]] = {}
        for r in conn.execute('SELECT alias,path FROM vault_aliases WHERE path IN (SELECT path FROM vault_docs WHERE user_id=?)', (uid,)).fetchall():
            aliases.setdefault(r['path'], []).append(r['alias'])
        links = []
        for r in conn.execute('SELECT src,dst_resolved FROM vault_links WHERE src IN (SELECT path FROM vault_docs WHERE user_id=?)', (uid,)).fetchall():
            if r['src'] in paths and r['dst_resolved'] in paths and r['src'] != r['dst_resolved']:
                links.append({'from': paths[r['src']], 'to': paths[r['dst_resolved']]})
        resolver = _memory_resolver(conn, uid)
        activity, agents = [], {}
        mentions: dict[str, int] = {}
        last_seen: dict[str, str] = {}
        for r in conn.execute('SELECT slug,body FROM vault_docs WHERE user_id=? AND kind="timeline" ORDER BY slug', (uid,)).fetchall():
            turns = journal.entries(r['body'] or '')
            if not turns:
                continue
            activity.append({'date': r['slug'], 'turns': len(turns)})
            for turn in turns:
                if turn['agent']:
                    agents[turn['agent']] = agents.get(turn['agent'], 0) + 1
                for key in _journal_keys(turn, resolver):
                    mentions[key] = mentions.get(key, 0) + 1
                    last_seen[key] = r['slug']
        names = _agent_names(conn, list(agents))
        entities = [{
            'key': paths[r['path']], 'type': r['type'], 'slug': r['slug'], 'title': r['title'] or r['slug'],
            'updated': r['updated'] or '', 'aliases': aliases.get(r['path'], []), 'facts': fact_rows(r['body'] or ''),
            'mentions': mentions.get(paths[r['path']], 0), 'last_seen': last_seen.get(paths[r['path']], ''),
        } for r in ents]
        return {'entities': entities, 'links': links, 'activity': activity,
                'agents': [{'id': a, 'name': names.get(a, a), 'turns': n} for a, n in sorted(agents.items(), key=lambda x: -x[1])]}

    return store.with_db(query)


def _agent_names(conn, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    return {r['id']: r['name'] for r in conn.execute(f'SELECT id,name FROM agents WHERE id IN ({marks})', ids).fetchall()}


@router.get('/memory/journal')
async def memory_journal_api(request: Request, _: AuthDep,
                             before: str | None = None, days: int = Query(10, ge=1, le=60),
                             entity: str | None = None, agent: str | None = None,
                             q: str | None = None, pending: bool = False):
    """Day notes newest first, split into turns, filtered and paged by day.
    ``next`` is the ``before`` value for the following page (None at the end)."""
    from datetime import date as _date
    from app.runtime.memory.vault import doc, index, journal, paths as vpaths
    uid = session_user_id(request)
    if before:
        try:
            before = _date.fromisoformat(before).isoformat()
        except ValueError:
            raise HTTPException(400, 'Invalid date')
    words = [w for w in (q or '').casefold().split() if w]
    entity = (entity or '').strip().casefold() or None

    def query(conn):
        index.rebuild(conn, uid)
        resolver = _memory_resolver(conn, uid)
        rows = conn.execute('SELECT slug,body FROM vault_docs WHERE user_id=? AND kind="timeline" AND slug < ? ORDER BY slug DESC',
                            (uid, before or '9999-12-31')).fetchall()
        out, more = [], False
        for r in rows:
            turns = []
            for turn in journal.entries(r['body'] or ''):
                turn['keys'] = _journal_keys(turn, resolver)
                if entity and entity not in turn['keys']:
                    continue
                if agent and turn['agent'] != agent:
                    continue
                if words and not journal.matches(turn, words):
                    continue
                turns.append(turn)
            if not turns:
                continue
            path = vpaths.timeline_path(uid, r['slug'])
            consolidated = path.is_file() and doc.parse(path.read_text(encoding='utf-8')).meta.get('consolidated') == 'true'
            if pending and consolidated:
                continue
            if len(out) == days:
                more = True
                break
            out.append({'date': r['slug'], 'consolidated': consolidated, 'entries': turns})
        sessions = list({t['session'] for d in out for t in d['entries'] if t['session']})
        titles = {}
        if sessions:
            marks = ','.join('?' * len(sessions))
            titles = {s['id']: s['title'] for s in conn.execute(
                f'SELECT id,title FROM sessions WHERE user_id=? AND id IN ({marks})', [uid, *sessions]).fetchall()}
        names = _agent_names(conn, list({t['agent'] for d in out for t in d['entries'] if t['agent']}))
        for d in out:
            for t in d['entries']:
                t['session_exists'] = t['session'] in titles
                t['session_title'] = titles.get(t['session'], '')
                t['agent_name'] = names.get(t['agent'], t['agent'])
        return {'days': out, 'next': out[-1]['date'] if more else None}

    return store.with_db(query)


@router.get('/memory/timeline')
async def memory_timeline_api(request: Request, _: AuthDep, date: str):
    from app.runtime.memory.vault import doc, paths
    try:
        path = paths.timeline_path(session_user_id(request), date)
    except ValueError:
        raise HTTPException(400, 'Invalid date')
    if not path.is_file():
        return {'date': date, 'blocks': [], 'raw': ''}
    raw = path.read_text(encoding='utf-8')
    return {'date': date, 'blocks': [line[2:] for line in doc.parse(raw).body.splitlines() if line.startswith('- ')], 'raw': raw}
