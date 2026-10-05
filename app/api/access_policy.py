"""Fail-closed HTTP perimeter, including mounted plugin applications.

Member endpoints are explicitly reviewed; new/global routes are Admin-only by
 default. Ownership remains mandatory even for Admins. This is API policy, not
an execution sandbox. Runtime admission must enforce the bound context too.
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Match
from starlette.staticfiles import StaticFiles

from app.core.deps import authenticated_user, require_owned_session
from app.runtime.access import AccessDenied, AccessUnavailable, execution_scope
from app.services import store

# Use actual matched endpoint identities, not URL prefixes (plugins can mount
# arbitrary URLs). Anonymous exemptions retain their own capability validation.
_PUBLIC = {
    "app.web.pages.login_page", "app.web.auth.login_submit", "app.web.pages.logout",
    "app.web.pages.shared_artifact_view_page", "app.api.rest.get_shared_artifact_raw",
    "app.api.rest.get_shared_artifact_download", "app.api.connector.connector_pair_http",
    "app.api.connector.connector_ws",
}
_BROKER = {
    "broker_bundles", "broker_connections", "broker_revoke", "broker_secret_request",
    "broker_connection_request", "broker_status", "broker_apply_file", "broker_http",
}
_MEMBER = {
    "app.api.rest": {
        "dashboard_prompts_api", "home_api", "home_live_api", "home_cards_api",
        "home_badges_api", "home_layout_api", "companion_snapshot_api", "companion_events_api",
        "list_episodes_api", "create_episode_api", "episode_contradictions_api",
        "episode_optimize_api", "episode_open_api", "episode_close_api", "get_episode_api",
        "episode_feedback_api", "list_agents", "get_agent", "list_sessions_api",
        "search_sessions_api", "get_session_api", "set_session_model_api",
        "get_session_reasoning_effort_api", "set_session_reasoning_effort_api", "create_session",
        "set_session_workplace_api", "delete_session_api", "prune_draft_sessions", "create_home_session",
        "update_session_agents", "session_chat_history", "session_chat_queries", "session_swarm_history",
        "list_session_attachments_api", "upload_session_attachment", "download_attachment", "delete_attachment_api",
        "session_context_usage", "session_chat_clear", "chat_history", "agent_context_usage", "chat_send", "chat_clear",
        "list_session_artifacts", "get_session_artifact", "delete_session_artifact", "create_session_artifact",
        "list_agent_artifacts_compat", "share_session_artifact", "get_session_artifact_share", "revoke_session_artifact_share",
        "memory_add_api", "memory_upload_api", "memory_graph_api", "memory_index_api", "memory_entity_api", "memory_forget_api",
        "memory_edit_api", "memory_move_api", "memory_overview_api", "memory_journal_api", "memory_timeline_api",
    },
    "app.api.platform": {
        "list_workplaces", "get_workplace", "list_models", "list_llm_profiles", "get_chat_model_options",
        "get_llm_profile", "list_schedules", "create_schedule", "get_schedule", "update_schedule",
        "delete_schedule", "list_schedule_runs", "run_schedule", "pause_schedule", "resume_schedule",
        "list_api_keys", "create_api_key", "delete_api_key", "list_telegram_links", "create_telegram_link_code", "unlink_telegram",
    },
    "app.api.access_routes": {
        "my_profile", "update_my_profile", "my_grants", "create_project", "share_project", "revoke_project_share",
        "chat_access", "update_chat_access", "picker_resources", "project_shares", "import_attachment",
    },
    "app.api.stream": {
        "session_chat_stream_post", "session_chat_stop", "session_chat_steer", "session_ui_action",
        "session_chat_stream", "chat_stream_post", "agent_chat_stop", "chat_stream", "agent_state",
    },
    "app.api.openai_compat": {"chat_completions"},
    "app.api.approvals": {"list_session_pending", "get_approval_mode", "put_approval_mode", "post_approval", "post_clarify"},
    "app.api.processes": {"list_processes", "process_detail", "process_logs", "stop_process", "close_process_monitoring"},
    "app.api.terminals": {"list_terminals", "create_terminal", "close_terminal", "attach_terminal"},
    "app.api.connections": {"submit_secret", "browser_bundles", "browser_connections", "browser_delete"},
    "app.web.pages": {"dashboard", "sessions_page", "companion_page", "memory_page", "artifact_view_page", "workplaces_page", "scheduler_page", "account_page"},
}
_EXECUTION = {"session_chat_stream_post", "session_ui_action", "session_chat_steer"}


def visible_agents(request: Request) -> list[dict]:
    user = authenticated_user(request)
    return store.list_agents() if user["role"] == "admin" else store.access.list_visible_agents(user["id"])


def require_agent(request: Request, agent_id: str) -> None:
    if not store.access.can_use(authenticated_user(request)["id"], "agent", agent_id):
        raise HTTPException(404, "Agent not found")


def require_attachments(session_id: str, attachment_ids: list[str]) -> None:
    from pathlib import Path
    from app.core.config import TOMO_HOME
    from app.services.chat import _looks_image_attachment, _looks_text_attachment, _looks_office_doc_attachment

    session = store.get_session(session_id)
    user = store.get_user((session or {}).get("user_id", ""))
    for attachment_id in attachment_ids:
        row = store.get_attachment(attachment_id)
        if not row or row.get("session_id") != session_id:
            raise HTTPException(404, "Attachment not found")
        root = (Path(TOMO_HOME) / "attachments" / session_id).absolute()
        path = Path(row["file_path"]).absolute()
        try:
            if root.resolve() != root or path.is_symlink():
                raise ValueError
            path.resolve().relative_to(root)
        except ValueError:
            raise HTTPException(404, "Attachment not found") from None
        if _looks_image_attachment(row) or not (_looks_text_attachment(row) or _looks_office_doc_attachment(row)):
            raise HTTPException(503, "Isolated image/binary preprocessing unavailable; import into the working folder for CLI processing")
        if _looks_office_doc_attachment(row):
            context = store.access.resolve_context(user["id"], session_id)
            if context.execution_mode != "restricted":
                raise HTTPException(503, "Document preprocessing requires a restricted local container")


def require_chat_inputs(context, message: str = "", attachment_ids=()) -> None:
    if context.role != "member":
        return
    from app.services.chat import resolve_slash_skill

    resolved = resolve_slash_skill(message)
    if resolved:
        skill, _ = resolved
        assigned = {s["id"] for s in store.get_agent_skills(context.agent_id) if s.get("assigned")}
        if skill["id"] not in assigned:
            raise HTTPException(403, "Skill is unavailable")
    require_attachments(context.session_id, list(attachment_ids))
    # Old history is also re-expanded, including after role/account changes.
    for entry in store.get_session_history(context.session_id):
        if entry.get("type") == "user":
            ids = entry.get("attachment_ids") or (entry.get("params") or {}).get("attachment_ids") or []
            require_attachments(context.session_id, ids)
            resolved = resolve_slash_skill(entry.get("content") or "")
            if resolved and resolved[0]["id"] not in {
                s["id"] for s in store.get_agent_skills(context.agent_id) if s.get("assigned")
            }:
                raise HTTPException(403, "Skill is unavailable")


def _matched_scope(routes, scope):
    # FastAPI supports both flat APIRoutes and lazy included-router branches.
    # Effective candidates retain include prefixes; original_router.routes do
    # not. Inspect the same candidates in order without mutating routing scope.
    for route in routes:
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            child = _matched_scope(candidates(), scope)
            if child is not None:
                return child
        else:
            match, child = route.matches(scope)
            if match == Match.FULL:
                return child
    return None


class AccessPolicyMiddleware:
    def __init__(self, app, router):
        self.app = app
        self.router = router

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        route_scope = _matched_scope(self.router.routes, scope)
        if route_scope is None:
            # Authentication also covers unknown paths and unsupported methods.
            route_scope = {}
        endpoint = route_scope.get("endpoint")
        module = getattr(endpoint, "__module__", "")
        name = getattr(endpoint, "__name__", "")
        identity = f"{module}.{name}"
        if identity in _PUBLIC or (isinstance(endpoint, StaticFiles) and scope["path"].startswith("/static/")):
            return await self.app(scope, receive, send)
        if module == "app.api.connections" and name in _BROKER:
            # Capability authentication checks current identity in broker_scope.
            return await self.app(scope, receive, send)
        # Terminal WS implements cookie authentication, revalidation and context
        # retention itself. No other unknown/member WS is admitted.
        if scope["type"] == "websocket" and identity == "app.api.terminals.attach_terminal":
            return await self.app(scope, receive, send)
        request = Request({**scope, "type": "http"}, receive=receive)
        context = None
        try:
            user = authenticated_user(request)
            if user["role"] != "admin" and name not in _MEMBER.get(module, set()):
                raise HTTPException(403, "Admin permission is required")
            if scope["type"] == "websocket" and user["role"] != "admin":
                raise HTTPException(403, "Admin permission is required")
            params = route_scope.get("path_params", {})
            sid = params.get("session_id") or request.query_params.get("session_id")
            if sid:
                require_owned_session(request, sid)
            aid = params.get("agent_id")
            if aid and user["role"] != "admin":
                require_agent(request, aid)
            if name in _EXECUTION and sid:
                if store.get_owned_session(sid, user["id"]):
                    context = store.access.resolve_context(user["id"], sid)
                else:
                    context = store.access.resolve_trusted_channel_context(user["id"], sid)
                if name in _EXECUTION:
                    require_chat_inputs(context)
        except (HTTPException, AccessDenied) as exc:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
                return
            if isinstance(exc, HTTPException):
                if exc.status_code == 303:
                    response = RedirectResponse(exc.headers["Location"], status_code=303)
                else:
                    response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            else:
                response = JSONResponse({"detail": "Execution access unavailable"},
                                        status_code=503 if isinstance(exc, AccessUnavailable) else 403)
            return await response(scope, receive, send)

        async def checked_send(message):
            # Long-lived SSE does not keep a disabled account authenticated.
            if message["type"] == "http.response.body" and message.get("more_body"):
                current = store.get_user(user["id"])
                if not current or not current["enabled"] or current["role"] != user["role"]:
                    raise AccessDenied("Account is unavailable")
            await send(message)

        if context is not None:
            with execution_scope(context):
                await self.app(scope, receive, checked_send)
        else:
            await self.app(scope, receive, checked_send)
