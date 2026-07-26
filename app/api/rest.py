"""JSON REST API — dashboard, agents, sessions, chat history."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.core.deps import AuthDep
from app.schemas import (
    AgentCreate,
    AgentUpdate,
    ChatMessageIn,
    HomeSessionIn,
    SessionChatIn,
    SessionCreate,
)
from app.services import store

router = APIRouter(prefix="/api")


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


@router.post("/agents")
async def create_agent(body: AgentCreate, _: AuthDep):
    try:
        return store.create_agent(body.model_dump())
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
        row["agent_name"] = " · ".join(names) if names else row.get("agent_id", "")
        row["is_swarm"] = len(ids) > 1
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
    return {
        **session,
        "agent_ids": ids,
        "agents": [agent_map[a] for a in ids if a in agent_map],
        "is_swarm": len(ids) > 1,
    }


@router.post("/sessions")
async def create_session(body: SessionCreate, _: AuthDep):
    try:
        session_id = store.create_swarm_session(body.agent_ids, body.user_id, body.coordinator_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"session_id": session_id}


@router.post("/sessions/home")
async def create_home_session(body: HomeSessionIn, _: AuthDep):
    """Start a coordinator-only chat from the dashboard home composer.

    No agent picker — always routes to the swarm coordinator (``is_super``).
    Optional ``message`` is returned so the client can deep-link
    ``/sessions?s=<id>&q=...`` and auto-send once.
    """
    try:
        created = store.create_home_session(body.user_id)
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
    user_id = request.query_params.get("user_id", "web")
    entries = store.get_history(agent_id, user_id)
    return {"entries": entries, "has_more": False}


@router.post("/agents/{agent_id}/chat")
async def chat_send(agent_id: str, body: ChatMessageIn, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    session_id = store.get_or_create_session(agent_id, body.user_id)
    return {"success": True, "session_id": session_id, "streaming": True}


@router.post("/agents/{agent_id}/chat/clear")
async def chat_clear(agent_id: str, request: Request, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    user_id = request.query_params.get("user_id", "web")
    store.clear_session(agent_id, user_id)
    return {"success": True}
