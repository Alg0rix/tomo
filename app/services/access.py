"""Current account/resource authorization, persistent grants and teardown barriers.

Legacy Store mutators are trusted internal primitives. API/runtime callers use
this service; tool arguments, approvals and prompts are never grant sources.
"""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from functools import wraps
import json
import math
import re
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from app.runtime.access import (
    AccessChangePending, AccessDenied, AccessUnavailable, ExecutionContext, ExecutionQuota, ResourceAccess,
)

_PERMISSION_RANK = {"read": 1, "read_write": 2}
_TYPES = {"workplace", "agent", "model", "unrestricted"}
# These existing tools mutate shared platform configuration or shared agent
# state. Runtime may replace a tool with a genuinely user-scoped implementation,
# but simply enabling it on the coordinator never gives Members Admin powers.
# ``use_skill``/``agent_state`` have user-scoped backends (per-user activation
# overlay / per-user state namespace), so they are no longer stripped here;
# the policy + backends enforce the scope.
_MEMBER_ADMIN_TOOLS = frozenset({
    "create_agent", "register_workplace", "plugin_manager", "manage_skill",
})


def _serialized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._mutation_lock:
            return method(self, *args, **kwargs)
    return guarded


class AccessService:
    def __init__(self, store) -> None:
        self.store = store
        self._mutation_lock = threading.RLock()
        self._stoppers: list[Callable[[str], None]] = []

    def require_user(self, user_id: str) -> dict:
        user = self.store.get_user(user_id)
        if not user or not user["enabled"] or user["role"] not in ("admin", "member"):
            raise AccessDenied("Account is unavailable")
        if user.get("access_pending"):
            raise AccessUnavailable("Account execution teardown is pending")
        return user

    def require_admin(self, user_id: str) -> dict:
        # A teardown barrier blocks execution, not the Admin recovery/control
        # plane. This lets an Admin retry their own failed quota/role teardown.
        user = self.store.get_user(user_id)
        if not user or not user["enabled"] or user["role"] != "admin":
            raise AccessDenied("Admin permission is required")
        return user

    def require_session(self, user_id: str, session_id: str) -> dict:
        self.require_user(user_id)
        session = self.store.get_owned_session(session_id, user_id)
        if not session:
            raise AccessDenied("Session is unavailable")
        if session["access_pending"]:
            raise AccessUnavailable("Session execution teardown is pending")
        return session

    def _grant(self, user_id: str, kind: str, rid: str) -> dict | None:
        def read(c):
            row = c.execute("SELECT * FROM resource_grants WHERE user_id=? AND resource_type=? AND resource_id=?", (user_id, kind, rid)).fetchone()
            return dict(row) if row else None
        return self.store.with_db(read)

    def workplace_permission(self, user_id: str, workplace_id: str) -> str:
        user = self.require_user(user_id)
        wp = self.store.get_workplace(workplace_id)
        if not wp or not wp["enabled"]:
            raise AccessDenied("Resource is unavailable")
        if self.store.with_db(lambda c: c.execute("SELECT access_pending FROM workplaces WHERE id=?", (workplace_id,)).fetchone()[0]):
            raise AccessUnavailable("Resource execution teardown is pending")
        if wp["storage_kind"] == "personal" and wp["owner_user_id"] != user_id:
            raise AccessDenied("Resource is unavailable")
        grant = self._grant(user_id, "workplace", workplace_id)
        if grant and grant["state"] != "active":
            raise AccessUnavailable("Resource access teardown is pending")
        if wp["owner_user_id"] == user_id:
            return "read_write"
        # Admin may administer resources but cannot automatically browse another
        # user's personal storage through picker/tool authorization.
        if user["role"] == "admin" and not wp["owner_user_id"]:
            return "read_write"
        if grant and grant["permission"] in _PERMISSION_RANK:
            return grant["permission"]
        raise AccessDenied("Resource is unavailable")

    def can_use(self, user_id: str, kind: str, rid: str) -> bool:
        try:
            user = self.require_user(user_id)
            if kind == "workplace":
                self.workplace_permission(user_id, rid)
                return True
            if kind not in ("agent", "model", "unrestricted"):
                return False
            grant = self._grant(user_id, kind, rid)
            if grant and grant["state"] != "active":
                return False
            if kind == "unrestricted":
                if grant:
                    return grant["permission"] == "use"
                if user["role"] != "admin":
                    return False
                # Admins already control the host. Keep their ordinary workflow
                # usable without per-destination setup, but never override a
                # deliberate revocation or another user's private/RO resource.
                revoked = self.store.with_db(lambda c: c.execute(
                    "SELECT 1 FROM access_audit WHERE subject_user_id=? AND resource_type='unrestricted' "
                    "AND destination_id=? AND action='grant.revoke' LIMIT 1", (user_id, rid),
                ).fetchone())
                return not revoked and self.workplace_permission(user_id, rid) == "read_write"
            obj = self.store.get_agent(rid) if kind == "agent" else self.store.get_llm_profile(rid)
            if not obj or not obj["enabled"]:
                return False
            if user["role"] == "admin":
                return True
            # Coordinator sharing shares capabilities, never user data/access.
            coordinator = self.store.get_coordinator() if kind == "agent" else None
            return bool((coordinator and coordinator["id"] == rid) or (grant and grant["permission"] == "use"))
        except AccessDenied:
            return False

    def require_use(self, user_id: str, kind: str, rid: str) -> None:
        self.require_user(user_id)
        grant = self._grant(user_id, kind, rid)
        if grant and grant['state'] == 'pending':
            raise AccessChangePending(f"Selected {kind} access change is pending managed execution teardown; an Admin must complete or recover the change")
        if not self.can_use(user_id, kind, rid):
            raise AccessDenied("Selected resource is unavailable")

    @staticmethod
    def _public_resource(wp: dict, permission: str) -> dict:
        fields = ("id", "name", "kind", "storage_kind", "owner_user_id", "destination_id", "enabled", "status", "online",
                  "connector_version", "remote_exec_ok", "remote_sandbox_ok")
        return {**{k: wp.get(k) for k in fields}, "permission": permission}

    def list_visible_workplaces(self, user_id: str) -> list[dict]:
        self.require_user(user_id)
        self.ensure_personal_space(user_id)
        result = []
        for wp in self.store.list_workplaces():
            try:
                permission = self.workplace_permission(user_id, wp["id"])
            except AccessDenied:
                continue
            result.append(self._public_resource(wp, permission))
        return result

    def list_visible_agents(self, user_id: str) -> list[dict]:
        self.require_user(user_id)
        fields = ("id", "name", "description", "role", "enabled", "is_super")
        return [{k: a.get(k) for k in fields} for a in self.store.list_agents() if self.can_use(user_id, "agent", a["id"])]

    def list_visible_models(self, user_id: str) -> list[dict]:
        self.require_user(user_id)
        return [{"id": p["id"], "name": p["name"], "model": p["model"], "available_models": p.get("available_models", [])}
                for p in self.store.list_llm_profiles() if self.can_use(user_id, "model", p["id"])]

    def _managed_project(self, user_id: str, name: str, storage_kind: str) -> dict:
        self.require_user(user_id)
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("Project name must contain 1–80 characters")
        def create(c):
            if storage_kind == "personal":
                row = c.execute("SELECT id FROM workplaces WHERE owner_user_id=? AND storage_kind='personal'", (user_id,)).fetchone()
                if row:
                    return row["id"]
            wid = "prj_" + uuid.uuid4().hex
            # Never derive disk paths from a user-supplied project name.
            from app.core.config import DB_PATH
            base = Path(self.store._path or DB_PATH).absolute().parent / "managed-storage"
            uid = hashlib.sha256(user_id.encode()).hexdigest()
            root = base / uid / wid
            for parent in (base, base / uid, root):
                if parent.is_symlink():
                    raise AccessUnavailable("Managed storage is unavailable")
                parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            now = time.time()
            c.execute("INSERT INTO workplaces(id,name,kind,status,host,root_path,owner_user_id,storage_kind,destination_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (wid, name, "local", "ready", "local", str(root.resolve()), user_id, storage_kind, "local", now, now))
            c.commit()
            return wid
        wid = self.store.with_db(create)
        wp = self.store.get_workplace(wid)
        assert wp is not None
        return self._public_resource(wp, "read_write")

    def ensure_personal_space(self, user_id: str) -> dict:
        return self._managed_project(user_id, "Personal space", "personal")

    def create_project(self, user_id: str, name: str) -> dict:
        out = self._managed_project(user_id, name, "project")
        self.audit(user_id, "project.create", destination_id=out["id"])
        return out

    @_serialized
    def share_project(self, actor_id: str, workplace_id: str, recipient_id: str, permission: str) -> dict:
        actor = self.require_user(actor_id)
        wp = self.store.get_workplace(workplace_id)
        if not wp or wp["storage_kind"] != "project" or (wp["owner_user_id"] != actor_id and actor["role"] != "admin"):
            raise AccessDenied("Project is unavailable")
        self.require_user(recipient_id)
        if permission not in _PERMISSION_RANK:
            raise ValueError("Permission must be read or read_write")
        if recipient_id == wp["owner_user_id"]:
            raise ValueError("Owner access cannot be replaced by a grant")
        return self._change_grant(actor_id, recipient_id, "workplace", workplace_id, permission)

    @_serialized
    def assign(self, actor_id: str, user_id: str, resource_type: str, resource_id: str, permission: str = "use") -> dict:
        self.require_admin(actor_id)
        self.require_user(user_id)
        if resource_type not in _TYPES:
            raise ValueError("Unknown resource type")
        if resource_type == "workplace":
            if permission not in _PERMISSION_RANK:
                raise ValueError("Workplace permission must be read or read_write")
        elif permission != "use":
            raise ValueError("Assignment permission must be use")
        obj = (self.store.get_agent(resource_id) if resource_type == "agent" else
               self.store.get_llm_profile(resource_id) if resource_type == "model" else
               self.store.get_workplace(resource_id))
        if not obj:
            raise AccessDenied("Resource is unavailable")
        if resource_type == "workplace" and obj["storage_kind"] == "personal":
            raise AccessDenied("Personal space cannot be shared; create a managed project")
        if resource_type == "workplace" and obj["owner_user_id"] == user_id:
            raise ValueError("Owner access cannot be replaced by a grant")
        return self._change_grant(actor_id, user_id, resource_type, resource_id, permission)

    def list_grants(self, actor_id: str, user_id: str) -> list[dict]:
        if actor_id != user_id:
            self.require_admin(actor_id)
        else:
            self.require_user(actor_id)
        return self.store.with_db(lambda c: [dict(r) for r in c.execute("SELECT * FROM resource_grants WHERE user_id=? ORDER BY resource_type,resource_id", (user_id,))])

    def register_execution_stopper(self, callback: Callable[[str], None]) -> None:
        """Trusted backend callback; return only after ALL retained access is gone."""
        if not callable(callback):
            raise TypeError("Execution stopper must be callable")
        if callback not in self._stoppers:
            self._stoppers.append(callback)

    def _mark_pending(self, session_ids: list[str]) -> None:
        def mark(c):
            c.executemany("UPDATE sessions SET access_pending=1,access_generation=access_generation+1 WHERE id=?", [(sid,) for sid in session_ids])
            c.commit()
        self.store.with_db(mark)

    def _stop(self, session_ids: list[str]) -> None:
        if session_ids and not self._stoppers:
            raise AccessUnavailable("Execution teardown backend is unavailable; access remains blocked")
        try:
            for sid in session_ids:
                for callback in tuple(self._stoppers):
                    result = callback(sid)
                    if result is not None:
                        raise RuntimeError("Teardown must confirm completion by returning None")
        except Exception as exc:
            raise AccessUnavailable("Execution teardown failed; access remains blocked") from exc

    def _clear_pending(self, session_ids: list[str]) -> None:
        def clear(c):
            c.executemany("UPDATE sessions SET access_pending=0 WHERE id=?", [(sid,) for sid in session_ids])
            c.commit()
        self.store.with_db(clear)

    def _principal_sessions(self, user_id: str) -> list[dict]:
        return self.store.list_sessions(user_id=user_id)

    def _affected_sessions(self, user_id: str, kind: str, rid: str) -> list[str]:
        sessions = self._principal_sessions(user_id)
        if kind in ("workplace", "unrestricted"):
            return [s["id"] for s in sessions if rid in [s["workplace_id"], *s["additional_workplace_ids"]]]
        return [s["id"] for s in sessions]

    @_serialized
    def _change_grant(self, actor: str, uid: str, kind: str, rid: str, permission: str | None) -> dict:
        old = self._grant(uid, kind, rid)
        implicit = bool(not old and kind == "unrestricted" and self.can_use(uid, kind, rid))
        affected = self._affected_sessions(uid, kind, rid) if old or implicit else []
        # Every changed grant rebuilds affected environments; even upgrades must
        # not silently widen already-running/delegated resource ceilings.
        teardown = bool((old and (old["permission"] != permission or old["state"] != "active"))
                        or (implicit and permission is None))
        if teardown:
            def pending(c):
                # Materialize an implicit Admin entitlement before teardown so
                # new chats cannot admit work while its revocation is pending.
                if implicit:
                    c.execute("INSERT INTO resource_grants(user_id,resource_type,resource_id,permission,state,granted_by,updated_at) VALUES (?,?,?,'use','pending',?,?)", (uid, kind, rid, actor, time.time()))
                else:
                    c.execute("UPDATE resource_grants SET state='pending' WHERE user_id=? AND resource_type=? AND resource_id=?", (uid, kind, rid))
                c.commit()
            self.store.with_db(pending)
            self._mark_pending(affected)
            self._stop(affected)
        def write(c):
            if permission is None:
                c.execute("DELETE FROM resource_grants WHERE user_id=? AND resource_type=? AND resource_id=?", (uid, kind, rid))
            else:
                c.execute("INSERT INTO resource_grants(user_id,resource_type,resource_id,permission,state,granted_by,updated_at) VALUES (?,?,?,?,'active',?,?) ON CONFLICT(user_id,resource_type,resource_id) DO UPDATE SET permission=excluded.permission,state='active',granted_by=excluded.granted_by,updated_at=excluded.updated_at", (uid, kind, rid, permission, actor, time.time()))
            c.commit()
        self.store.with_db(write)
        if teardown:
            self._clear_pending(affected)
        self.audit(actor, "grant.revoke" if permission is None else "grant.assign", destination_id=rid,
                   subject_user_id=uid, resource_type=kind, permission=permission or "")
        return {"user_id": uid, "resource_type": kind, "resource_id": rid, "permission": permission, "state": "revoked" if permission is None else "active"}

    @_serialized
    def revoke(self, actor_id: str, user_id: str, resource_type: str, resource_id: str) -> dict:
        actor = self.require_user(actor_id)
        if actor["role"] != "admin":
            wp = self.store.get_workplace(resource_id) if resource_type == "workplace" else None
            if not wp or wp["storage_kind"] != "project" or wp["owner_user_id"] != actor_id:
                raise AccessDenied("Grant is unavailable")
        if resource_type not in _TYPES:
            raise ValueError("Unknown resource type")
        return self._change_grant(actor_id, user_id, resource_type, resource_id, None)

    @contextmanager
    def execution_guard(self, context: ExecutionContext):
        """Atomic admission fence. Register supervised work before releasing.

        Backend must use this around revalidation + spawn/handle registration,
        not release it between check and registration. Revocation uses the same
        fence, then blocks admission before confirmed termination.
        """
        with self._mutation_lock:
            yield self.revalidate(context)

    @_serialized
    def change_workplace(self, workplace_id: str, operation):
        sessions = [s["id"] for s in self.store.list_sessions()
                    if workplace_id in [s["workplace_id"], *s["additional_workplace_ids"]]]
        def pending(c):
            c.execute("UPDATE workplaces SET access_pending=1 WHERE id=?", (workplace_id,))
            c.commit()
        self.store.with_db(pending)
        self._mark_pending(sessions)
        self._stop(sessions)
        result = operation()
        self.store.with_db(lambda c: (c.execute("UPDATE workplaces SET access_pending=0 WHERE id=?", (workplace_id,)), c.commit()))
        self._clear_pending(sessions)
        return result

    @_serialized
    def stop_user_execution(self, user_id: str) -> list[str]:
        """Barrier for role/enable/delete/quota mutations; failures stay pending."""
        sessions = [s["id"] for s in self._principal_sessions(user_id)]
        def mark(c):
            c.execute("UPDATE users SET access_pending=1 WHERE id=?", (user_id,))
            c.commit()
        self.store.with_db(mark)
        self._mark_pending(sessions)
        self._stop(sessions)
        return sessions

    def finish_user_change(self, user_id: str, session_ids: list[str]) -> None:
        self.store.with_db(lambda c: (c.execute("UPDATE users SET access_pending=0 WHERE id=?", (user_id,)), c.commit()))
        self._clear_pending(session_ids)

    @_serialized
    def set_chat_access(self, user_id: str, session_id: str, active_workplace_id: str,
                        additional_workplace_ids=(), execution_mode: str = "restricted",
                        unrestricted_acknowledged: bool = False) -> dict:
        user = self.require_user(user_id)
        # Pending sessions may retry the user-initiated access update/teardown.
        session = self.store.get_owned_session(session_id, user_id)
        if not session:
            raise AccessDenied("Session is unavailable")
        if execution_mode not in ("restricted", "unrestricted"):
            raise ValueError("Unknown execution mode")
        ids = list(dict.fromkeys([active_workplace_id, *additional_workplace_ids]))
        if not active_workplace_id:
            raise ValueError("An active workplace is required")
        self.workplace_permission(user_id, active_workplace_id)
        for wid in ids:
            if wid == active_workplace_id:
                continue
            # Grants are still required for every enabled resource, but
            # resources on other machines are transfer endpoints only: they
            # are never mounted at the execution destination. Moving bytes
            # between machines additionally requires an explicit authorized
            # transfer (source read + destination write + audit).
            self.workplace_permission(user_id, wid)
            other = self.store.get_workplace(wid)
            if not other:
                raise AccessDenied("Resource is unavailable")
        if execution_mode == "unrestricted":
            self.require_use(user_id, "unrestricted", active_workplace_id)
            if user["role"] != "admin" and not unrestricted_acknowledged:
                raise AccessDenied("Explicit unrestricted execution acknowledgement is required")
        additional = [wid for wid in ids if wid != active_workplace_id]
        if (session["workplace_id"] == active_workplace_id and session["additional_workplace_ids"] == additional
                and session["execution_mode"] == execution_mode and not session["access_pending"]):
            return session
        self._mark_pending([session_id])
        self._stop([session_id])
        def update(c):
            c.execute("UPDATE sessions SET workplace_id=?,additional_workplace_ids_json=?,execution_mode=?,access_pending=0,updated_at=? WHERE id=? AND user_id=?", (active_workplace_id, json.dumps(additional), execution_mode, time.time(), session_id, user_id))
            c.commit()
        self.store.with_db(update)
        self.audit(user_id, "chat.access", session_id=session_id, destination_id=active_workplace_id,
                   subject_user_id=user_id, permission=execution_mode)
        return self.store.get_owned_session(session_id, user_id)

    def default_execution_mode(self, user_id: str, workplace_id: str) -> str:
        """Admins keep host workflows; Members never inherit that default."""
        user = self.require_user(user_id)
        if user['role'] == 'admin':
            grant = self._grant(user_id, 'unrestricted', workplace_id)
            if grant and grant['state'] == 'pending':
                raise AccessChangePending("Admin host access change is pending managed execution teardown; complete or recover it before creating a chat")
            if self.can_use(user_id, 'unrestricted', workplace_id):
                return 'unrestricted'
        return 'restricted'

    def initialize_chat(self, user_id: str, session_id: str, workplace_id: str = "") -> None:
        """Only used immediately after creation, before any execution starts."""
        self.require_user(user_id)
        wid = workplace_id or self.ensure_personal_space(user_id)["id"]
        self.workplace_permission(user_id, wid)
        mode = self.default_execution_mode(user_id, wid)
        def initialize(c):
            c.execute("UPDATE sessions SET workplace_id=?,execution_mode=? WHERE id=? AND user_id=? AND access_generation=0 AND message_count=0", (wid, mode, session_id, user_id))
            c.commit()
        self.store.with_db(initialize)
        # Choose a currently ASSIGNED profile for a new Member chat when the
        # shared coordinator's global default is not assigned to this account.
        # Persist the choice once: later revocation must deny, not silently
        # switch an existing conversation to some other profile.
        user = self.require_user(user_id)
        if user["role"] == "member" and not self.store.resolve_session_llm_profile(session_id):
            models = self.list_visible_models(user_id)
            if models:
                self.store.set_session_model(session_id, models[0]["id"], models[0]["model"])

    def _agent_allows(self, agent: dict, wp: dict) -> bool:
        # Managed user data is a normal chat capability, independent of shared
        # coordinator's external host bindings. Explicit bindings constrain
        # external resources; is_super never overrides these checks.
        if wp["storage_kind"] in ("personal", "project"):
            return True
        scope = agent.get("workplace_scope", "single")
        if scope == "single" and not agent.get("workplace_id") and not agent.get("workplace_ids"):
            return True  # Unbound profiles operate on the authorized chat scope.
        return bool(scope == "all" or (scope == "all_tunnels" and wp["kind"] == "tunnel")
                    or wp["id"] == agent.get("workplace_id") or wp["id"] in agent.get("workplace_ids", []))

    def resolve_context(self, user_id: str, session_id: str, agent_id: str | None = None, *, parent: ExecutionContext | None = None) -> ExecutionContext:
        user = self.require_user(user_id)
        session = self.require_session(user_id, session_id)
        aid = agent_id or session["coordinator_id"]
        self.require_use(user_id, "agent", aid)
        agent = self.store.get_agent(aid)
        profile = self.store.resolve_session_llm_profile(session_id, aid)
        if profile:
            self.require_use(user_id, "model", profile["id"])
        elif user["role"] == "member":
            raise AccessUnavailable("An assigned model is required")
        mode = session["execution_mode"]
        if mode not in ("restricted", "unrestricted"):
            raise AccessDenied("Execution mode is unavailable")
        active = session["workplace_id"]
        if not active:
            raise AccessDenied("Select an authorized working location")
        destination = active
        if mode == "unrestricted":
            self.require_use(user_id, "unrestricted", active)
        resources = []
        destination_machine = None
        for wid in dict.fromkeys([active, *session["additional_workplace_ids"]]):
            if not wid:
                continue
            permission = self.workplace_permission(user_id, wid)
            wp = self.store.get_workplace(wid)
            if wp["kind"] in ("ssh", "tunnel") and not wp.get("online"):
                raise AccessUnavailable("Execution destination is offline")
            if not self._agent_allows(agent, wp):
                if wid == active:
                    raise AccessDenied("Agent cannot use the active working location")
                continue
            if destination_machine is None:
                destination_machine = wp["destination_id"]
            # Resources on other machines are transfer endpoints, never
            # execution mounts. They stay in the ceiling so explicit
            # authorized transfers can use them; container/remote dispatch
            # mounts only same-machine resources.
            transfer_only = bool(wp["destination_id"] != destination_machine)
            if not transfer_only and mode == "restricted" and wp["kind"] == "local" and wp["storage_kind"] == "external":
                from app.core.config import DB_PATH, TOMO_HOME

                configured_root = Path(wp["root_path"]).expanduser()
                if not wp["root_path"] or not configured_root.is_absolute() or not configured_root.is_dir():
                    raise AccessUnavailable("Execution resource path is unavailable")
                root = configured_root.resolve()
                protected = (Path(TOMO_HOME).resolve(), Path(self.store._path or DB_PATH).resolve().parent)
                if any(root == private or root in private.parents or private in root.parents for private in protected):
                    raise AccessDenied("Resource overlaps protected server storage")
            resources.append(ResourceAccess(wid, wp["root_path"], permission, wp["destination_id"], wp["kind"], wp["storage_kind"], f"/workplaces/{wid}", transfer_only))
        tools = frozenset(t["id"] for t in self.store.get_agent_tools(aid) if t.get("enabled"))
        if user["role"] == "member":
            tools -= _MEMBER_ADMIN_TOOLS
            tools = self._explicit_member_external_scope(aid, tools)
        context = ExecutionContext(user_id, session_id, aid, user["role"], active, tuple(resources), mode,
                                   destination, session["access_generation"], self.get_quota(user_id), tools)
        if parent:
            if parent.user_id != user_id or parent.session_id != session_id or parent.access_generation != context.access_generation:
                raise AccessDenied("Execution ceiling is no longer valid")
            if context.execution_mode != parent.execution_mode or context.destination_id != parent.destination_id:
                raise AccessDenied("Delegation cannot change execution destination or mode")
            ceiling = {r.workplace_id: r for r in parent.resources}
            narrowed = []
            for resource in context.resources:
                old = ceiling.get(resource.workplace_id)
                if old and (old.root_path, old.destination_id, old.kind) == (resource.root_path, resource.destination_id, resource.kind):
                    permission = min((old.permission, resource.permission), key=_PERMISSION_RANK.__getitem__)
                    narrowed.append(replace(resource, permission=permission))
            if active and active not in {r.workplace_id for r in narrowed}:
                raise AccessDenied("Active working location is outside the execution ceiling")
            context = replace(context, resources=tuple(narrowed), tool_ids=context.tool_ids & parent.tool_ids)
        return context

    def _explicit_member_external_scope(self, agent_id: str, tools: frozenset) -> frozenset:
        """Keep Member external tools only with explicit per-agent assignment.

        Builtins stay on the existing default opt-in, but ``mcp__*`` and
        ``plugin__*`` ids enter a Member ceiling only when ``agent_tools``
        holds an explicit enabled row for that agent. A brand-new
        server/plugin is therefore never implicitly Member-callable; an
        Admin assigns it on the agent first. Fail closed, no host fallback.
        """
        external = {t for t in tools if t.startswith(("mcp__", "plugin__"))}
        if not external:
            return tools
        rows = self.store.with_db(
            lambda c: c.execute(
                "SELECT tool_id FROM agent_tools WHERE agent_id=? AND enabled=1",
                (agent_id,),
            ).fetchall()
        )
        explicit = {r["tool_id"] for r in rows}
        return frozenset(t for t in tools if not t.startswith(("mcp__", "plugin__")) or t in explicit)

    def revalidate(self, context: ExecutionContext | None) -> ExecutionContext:
        # A missing/None ceiling is never an implicit host or anonymous context.
        if context is None:
            raise AccessDenied("Execution identity is unavailable")
        return self.resolve_context(context.user_id, context.session_id, context.agent_id, parent=context)

    def require_admin_action(self, context: ExecutionContext) -> ExecutionContext:
        current = self.revalidate(context)
        self.require_admin(current.user_id)
        return current

    def require_tool(self, context: ExecutionContext, tool_name: str) -> ExecutionContext:
        current = self.revalidate(context)
        if tool_name not in current.tool_ids:
            raise AccessDenied("Tool is outside the execution ceiling")
        return current

    def authorize_resource(self, context: ExecutionContext, resource_id: str, *, write: bool = False) -> ResourceAccess:
        current = self.revalidate(context)
        for resource in current.resources:
            if resource.workplace_id == resource_id and (not write or resource.writable):
                return resource
        raise AccessDenied("Resource is outside the execution ceiling")

    def authorize_transfer(self, context: ExecutionContext, source_id: str, destination_id: str) -> tuple[dict, dict]:
        # Transfers require current user grants even when the remote resource
        # cannot be mounted in this chat. Activation is still required; a future
        # transfer UI should use explicit per-transfer enabled scope.
        self.authorize_resource(context, source_id)
        self.authorize_resource(context, destination_id, write=True)
        source, target = self.store.get_workplace(source_id), self.store.get_workplace(destination_id)
        self.audit(context.user_id, "resource.transfer", session_id=context.session_id, agent_id=context.agent_id, destination_id=destination_id)
        return source, target

    def get_quota(self, user_id: str) -> ExecutionQuota:
        self.require_user(user_id)
        row = self.store.with_db(lambda c: c.execute("SELECT * FROM user_execution_quotas WHERE user_id=?", (user_id,)).fetchone())
        values = {k: row[k] for k in asdict(ExecutionQuota())} if row else {}
        if "gpu_allowed" in values:
            values["gpu_allowed"] = bool(values["gpu_allowed"])
        return ExecutionQuota(**values)

    @_serialized
    def set_quota(self, admin_id: str, user_id: str, values: dict) -> ExecutionQuota:
        self.require_admin(admin_id)
        if not self.store.get_user(user_id):
            raise AccessDenied("Account is unavailable")
        if set(values) - set(asdict(ExecutionQuota())):
            raise ValueError("Unknown quota field")
        # A partial Admin edit must not silently reset other enforced limits.
        # Read persisted values directly so retrying a pending teardown remains
        # a control-plane operation, not fresh execution admission.
        row = self.store.with_db(lambda c: c.execute("SELECT * FROM user_execution_quotas WHERE user_id=?", (user_id,)).fetchone())
        current = {k: row[k] for k in asdict(ExecutionQuota())} if row else {}
        if "gpu_allowed" in current:
            current["gpu_allowed"] = bool(current["gpu_allowed"])
        quota = replace(ExecutionQuota(**current), **values)
        for key, value in asdict(quota).items():
            if key == "gpu_allowed":
                if not isinstance(value, bool):
                    raise ValueError("GPU assignment must be boolean")
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or (key != "cpu" and not isinstance(value, int)):
                raise ValueError("Quotas must be positive finite numbers")
        sessions = self.stop_user_execution(user_id)
        def update(c):
            c.execute("INSERT INTO user_execution_quotas(user_id,cpu,memory_mb,disk_mb,duration_seconds,gpu_allowed) VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET cpu=excluded.cpu,memory_mb=excluded.memory_mb,disk_mb=excluded.disk_mb,duration_seconds=excluded.duration_seconds,gpu_allowed=excluded.gpu_allowed", (user_id, *asdict(quota).values()))
            c.commit()
        self.store.with_db(update)
        self.finish_user_change(user_id, sessions)
        self.audit(admin_id, "quota.configure", subject_user_id=user_id)
        return quota

    def audit(self, actor_id: str, action: str, *, session_id: str = "", agent_id: str = "",
              destination_id: str = "", outcome: str = "ok", job_id: str = "",
              subject_user_id: str = "", resource_type: str = "", permission: str = "") -> None:
        # Only identifiers and short action/outcome labels; never arbitrary
        # details, arguments, environment, stderr or file content.
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,100}", action) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,40}", outcome):
            raise ValueError("Invalid audit label")
        def write(c):
            c.execute("INSERT INTO access_audit(actor_id,session_id,job_id,agent_id,action,destination_id,outcome,created_at,subject_user_id,resource_type,permission) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (actor_id, session_id, job_id, agent_id, action, destination_id, outcome, time.time(), subject_user_id, resource_type, permission))
            c.commit()
        self.store.with_db(write)

    @_serialized
    def create_account(self, admin_id: str, data: dict) -> dict:
        self.require_admin(admin_id)
        user = self.store.create_user(data)
        self.audit(admin_id, "account.create", subject_user_id=user["id"], permission=user["role"])
        return user

    @_serialized
    def update_account(self, admin_id: str, user_id: str, data: dict) -> dict | None:
        self.require_admin(admin_id)
        return self.store.update_user(user_id, data, actor_id=admin_id)

    @_serialized
    def delete_account(self, admin_id: str, user_id: str) -> bool:
        self.require_admin(admin_id)
        return self.store.delete_user(user_id, actor_id=admin_id)

    @_serialized
    def update_personal_profile(self, user_id: str, data: dict) -> dict:
        self.require_user(user_id)
        if set(data) - {"display_name", "password"}:
            raise AccessDenied("Only personal profile/password changes are permitted")
        user = self.store.update_user(user_id, data)
        self.audit(user_id, "account.profile", subject_user_id=user_id)
        return user

    def list_personal_api_keys(self, user_id: str) -> list[dict]:
        self.require_user(user_id)
        return self.store.list_api_keys(user_id)

    @_serialized
    def create_personal_api_key(self, user_id: str, name: str = "") -> dict:
        self.require_user(user_id)
        key = self.store.create_api_key(user_id, name)
        self.audit(user_id, "key.create", subject_user_id=user_id)
        return key

    @_serialized
    def delete_personal_api_key(self, user_id: str, key_id: str) -> bool:
        self.require_user(user_id)
        key = self.store.get_api_key(key_id)
        if not key or key["user_id"] != user_id:
            raise AccessDenied("API key is unavailable")
        result = self.store.delete_api_key(key_id)
        self.audit(user_id, "key.delete", subject_user_id=user_id)
        return result

    def list_visible_schedules(self, user_id: str) -> list[dict]:
        self.require_user(user_id)
        return [s for s in self.store.list_schedules() if s["owner_user_id"] == user_id]

    def require_schedule(self, user_id: str, schedule_id: str) -> dict:
        self.require_user(user_id)
        schedule = self.store.get_schedule(schedule_id)
        if not schedule or schedule["owner_user_id"] != user_id:
            raise AccessDenied("Schedule is unavailable")
        return schedule

    @_serialized
    def create_schedule_for_context(self, context: ExecutionContext, data: dict) -> dict:
        current = self.revalidate(context)
        aid = str(data.get("agent_id") or current.agent_id)
        delegated = self.resolve_context(current.user_id, current.session_id, aid, parent=current)
        schedule = self.store.create_schedule({**data, "agent_id": aid, "owner_user_id": current.user_id,
                                              "execution_context": delegated.to_dict()})
        self.audit(current.user_id, "schedule.create", session_id=current.session_id, agent_id=aid)
        return schedule

    def list_audit(self, admin_id: str, user_id: str | None = None, limit: int = 100) -> list[dict]:
        self.require_admin(admin_id)
        limit = min(max(int(limit), 1), 1000)
        def read(c):
            rows = c.execute("SELECT * FROM access_audit WHERE (? IS NULL OR actor_id=? OR subject_user_id=?) ORDER BY id DESC LIMIT ?", (user_id, user_id, user_id, limit))
            return [dict(row) for row in rows]
        return self.store.with_db(read)


class _AccessProxy:
    def __getattr__(self, name):
        from app.services.store import store
        return getattr(store.access, name)


access = _AccessProxy()
