"""Account-owned access controls; all mutations use foundation policy services."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import AdminDep, AuthDep, authenticated_user, require_owned_session, session_user_id
from app.services import store

router = APIRouter(prefix="/api", tags=["access"])


class PersonalProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=160)
    password: str | None = Field(default=None, min_length=8, max_length=512)


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


class ShareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str | None = Field(default=None, min_length=1, max_length=80)
    username: str | None = Field(default=None, min_length=1, max_length=80)
    permission: Literal["read", "read_write"] = "read"


class AssignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permission: Literal["use", "read", "read_write"] = "use"


class ChatAccessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_workplace_id: str = Field(min_length=1, max_length=80)
    additional_workplace_ids: list[str] = Field(default_factory=list, max_length=100)
    execution_mode: Literal["restricted", "unrestricted"] = "restricted"
    unrestricted_acknowledged: bool = False


@router.get("/me")
async def my_profile(request: Request, _: AuthDep):
    return authenticated_user(request)


@router.put("/me")
async def update_my_profile(body: PersonalProfileIn, request: Request, _: AuthDep):
    try:
        return await asyncio.to_thread(store.access.update_personal_profile, session_user_id(request),
                                       body.model_dump(exclude_unset=True, exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, "Invalid profile change") from exc


@router.get("/me/grants")
async def my_grants(request: Request, _: AuthDep):
    uid = session_user_id(request)
    # Removed/pending/disabled resources do not reveal their ids in autocomplete.
    workplaces = {w["id"] for w in store.access.list_visible_workplaces(uid)}
    return {"grants": [g for g in store.access.list_grants(uid, uid)
                       if g["state"] == "active" and (
                           g["resource_id"] in workplaces if g["resource_type"] in {"workplace", "unrestricted"}
                           else store.access.can_use(uid, g["resource_type"], g["resource_id"]))]}


@router.post("/projects", status_code=201)
async def create_project(body: ProjectIn, request: Request, _: AuthDep):
    return {"workplace": store.access.create_project(session_user_id(request), body.name)}


@router.post("/projects/{workplace_id}/shares")
async def share_project(workplace_id: str, body: ShareIn, request: Request, _: AuthDep):
    # Check project administration BEFORE recipient lookup: no account directory
    # or project metadata is exposed to recipients who cannot reshare.
    _owned_project(session_user_id(request), workplace_id)
    if bool(body.user_id) == bool(body.username):
        raise HTTPException(400, "Specify a recipient username or user id")
    recipient = store.get_user(body.user_id) if body.user_id else store.get_user_by_username(body.username)
    if not recipient or not recipient["enabled"]:
        raise HTTPException(404, "Recipient unavailable")
    return await asyncio.to_thread(store.access.share_project, session_user_id(request), workplace_id,
                                   recipient["id"], body.permission)


def _owned_project(actor_id: str, workplace_id: str):
    user = store.access.require_user(actor_id)
    project = store.get_workplace(workplace_id)
    if not project or project["storage_kind"] != "project" or (
        project["owner_user_id"] != actor_id and user["role"] != "admin"
    ):
        raise HTTPException(403, "Project administration unavailable")
    return project


@router.get("/projects/{workplace_id}/shares")
async def project_shares(workplace_id: str, request: Request, _: AuthDep):
    _owned_project(session_user_id(request), workplace_id)
    def read(c):
        return [dict(row) for row in c.execute(
            "SELECT g.user_id, u.username, g.permission, g.state FROM resource_grants g "
            "JOIN users u ON u.id=g.user_id WHERE g.resource_type='workplace' AND g.resource_id=? "
            "AND g.state IN ('active','pending') ORDER BY u.username", (workplace_id,))]
    return {"shares": store.with_db(read)}


@router.get("/access/resources")
async def picker_resources(request: Request, _: AuthDep):
    uid = session_user_id(request)
    return {"workplaces": store.access.list_visible_workplaces(uid)}


@router.delete("/projects/{workplace_id}/shares/{user_id}")
async def revoke_project_share(workplace_id: str, user_id: str, request: Request, _: AuthDep):
    return await asyncio.to_thread(store.access.revoke, session_user_id(request), user_id, "workplace", workplace_id)


@router.get("/sessions/{session_id}/access")
async def chat_access(session_id: str, request: Request, _: AuthDep):
    session = require_owned_session(request, session_id)
    legacy = (authenticated_user(request)["role"] == "admin" and not session.get("workplace_id")
              and session.get("access_generation") == 0)
    return {"active_workplace_id": session.get("workplace_id"),
            "additional_workplace_ids": session.get("additional_workplace_ids", []),
            "execution_mode": "unrestricted" if legacy else session.get("execution_mode", "restricted"),
            "legacy_unrestricted": legacy,
            "destination_id": "__legacy_host__" if legacy else session.get("workplace_id"),
            "access_pending": session.get("access_pending", False),
            "unrestricted_workplace_ids": [w["id"] for w in store.access.list_visible_workplaces(session_user_id(request))
                                           if store.access.can_use(session_user_id(request), "unrestricted", w["id"])],
            "workplaces": store.access.list_visible_workplaces(session_user_id(request))}


@router.put("/sessions/{session_id}/access")
async def update_chat_access(session_id: str, body: ChatAccessIn, request: Request, _: AuthDep):
    require_owned_session(request, session_id)
    return await asyncio.to_thread(store.access.set_chat_access, session_user_id(request), session_id,
                                   **body.model_dump())


@router.post("/attachments/{attachment_id}/import")
async def import_attachment(attachment_id: str, request: Request, _: AuthDep):
    attachment = store.get_attachment(attachment_id)
    if not attachment:
        raise HTTPException(404, "Attachment not found")
    require_owned_session(request, attachment["session_id"])
    context = store.access.resolve_context(session_user_id(request), attachment["session_id"])
    from app.runtime.access import execution_scope
    from app.runtime.isolation.attachments import import_upload
    from app.runtime.supervision import admitted_turn
    with execution_scope(context):
        async with admitted_turn(context):
            path = await asyncio.to_thread(import_upload, attachment)
    return {"path": path, "workplace_id": context.active_workplace_id}


@router.get("/users/{user_id}/grants")
async def user_grants(user_id: str, request: Request, _: AdminDep):
    return {"grants": store.access.list_grants(session_user_id(request), user_id)}


@router.get("/users/{user_id}/unrestricted-destinations")
async def unrestricted_destinations(user_id: str, request: Request, _: AdminDep):
    """Admin grant catalog, not permission to browse another personal space."""
    actor = authenticated_user(request)
    store.access.require_user(user_id)
    candidates = {w["id"]: w for w in store.access.list_visible_workplaces(actor["id"])
                  if w["kind"] == "local" and store.access.can_use(user_id, "workplace", w["id"])}
    # Default Member personal space needs an Admin-issued destination grant.
    # Expose only its opaque ID and generic title, never its private name/path.
    for wp in store.list_workplaces():
        if (wp["kind"] == "local" and wp["storage_kind"] == "personal"
                and wp["owner_user_id"] == user_id and store.access.can_use(user_id, "workplace", wp["id"])):
            candidates[wp["id"]] = {**store.access._public_resource(wp, "read_write"),
                                    "name": "Selected account's personal space"}
    return {"workplaces": list(candidates.values())}


@router.put("/users/{user_id}/grants/{resource_type}/{resource_id}")
async def assign_resource(user_id: str, resource_type: Literal["agent", "model", "workplace", "unrestricted"],
                          resource_id: str, body: AssignmentIn, request: Request, _: AdminDep):
    try:
        return await asyncio.to_thread(store.access.assign, session_user_id(request), user_id, resource_type,
                                       resource_id, body.permission)
    except ValueError as exc:
        raise HTTPException(400, "Invalid assignment") from exc


@router.delete("/users/{user_id}/grants/{resource_type}/{resource_id}")
async def revoke_resource(user_id: str, resource_type: Literal["agent", "model", "workplace", "unrestricted"],
                          resource_id: str, request: Request, _: AdminDep):
    return await asyncio.to_thread(store.access.revoke, session_user_id(request), user_id, resource_type, resource_id)


@router.get("/users/{user_id}/quota")
async def user_quota(user_id: str, request: Request, _: AdminDep):
    return asdict(store.access.get_quota(user_id))


@router.put("/users/{user_id}/quota")
async def set_user_quota(user_id: str, body: dict, request: Request, _: AdminDep):
    try:
        return asdict(await asyncio.to_thread(store.access.set_quota, session_user_id(request), user_id, body))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Invalid quota") from exc


@router.get("/access/audit")
async def access_audit(request: Request, _: AdminDep, user_id: str | None = None,
                       limit: int = Query(100, ge=1, le=1000)):
    return {"events": store.access.list_audit(session_user_id(request), user_id, limit)}
