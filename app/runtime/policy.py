"""Runtime authorization shared by dispatch, durable work and direct services.

No prompt, approval token or agent coordinator bit is an authority. Tools not
implemented against a resource boundary stay unavailable in restricted mode.
"""
from __future__ import annotations

from app.runtime.access import AccessDenied, AccessUnavailable, current_execution


def _is_mcp_tool_name(name: str) -> bool:
    from app.runtime.mcp.names import is_mcp_runtime_id

    return is_mcp_runtime_id(name)


def _require_mcp_enabled(name: str) -> None:
    """Fail closed on forged/disabled MCP ids (server + item enablement)."""
    from app.runtime.mcp.names import split_runtime_tool_id
    from app.services import store

    parsed = split_runtime_tool_id(name)
    if parsed is None:
        raise AccessDenied("Unknown external tool")
    server_id, _tool_part = parsed
    item = store.get_mcp_item_by_runtime_id(name)
    if item is None or item["kind"] != "tool" or not item["enabled"]:
        raise AccessDenied("MCP tool disabled or unavailable")
    server = store.get_mcp_server(server_id)
    if server is None or not server["enabled"]:
        raise AccessDenied("MCP tool disabled or unavailable")


def _require_plugin_enabled(name: str) -> None:
    """Fail closed unless the plugin tool is currently enabled."""
    from app.plugins.manager import get_manager

    if name not in get_manager().definitions():
        raise AccessDenied("External tool is unavailable")

# These mutate shared/control-plane state, even in explicitly unrestricted mode.
ADMIN_TOOLS = frozenset({
    "create_agent", "register_workplace", "plugin_manager", "manage_skill",
    "use_skill", "agent_state",
})
# Member-eligible tools with genuinely user-scoped implementations. Members
# never touch the shared surface: MCP stdio/HTTP calls run in the caller's
# per-chat container (or a sanitized one-shot HTTP session), plugin tools
# replay source-only in that container, skill reads are gated to the
# Member's effective set with per-user activation overlay, and agent_state
# is namespaced per user. Management (install/enable/discover) stays Admin.
MEMBER_SANDBOXED_TOOLS = frozenset({
    "use_skill", "agent_state",
})
# Reviewed builtins only. New plugins/MCP/tools need an explicit service boundary.
RESTRICTED_TOOLS = frozenset({
    "bash", "runpy", "read_file", "write_file", "str_replace", "patch",
    "list_dir", "search_files", "delete_file", "process", "delegate",
    "start_swarm", "swarm_board", "todo", "clarify", "render_ui", "schedule",
    "list_workplaces", "session_search", "memory", "record_episode",
    "recall_episodes", "save_artifact", "list_artifacts", "fetch_artifact",
    "telegram_send_file", "list_skills", "agent_info",
    # Stage 4: explicit authorized transfers (source read + dest write +
    # audit) under the chat ceiling; not a mount or a privilege.
    "portal",
    # Stage 5: owned retrieval tools behind their own service boundaries
    # (scoped egress policy for network; assigned-profile vision routing
    # with supervised preprocessing). Authorization here admits them to the
    # ceiling; each backend re-checks its boundary before acting.
    "web_search", "web_fetch", "vision_analyze",
    # Browser: offline container rendering always; online fetch-then-render
    # under scoped egress with per-hop SSRF (subresources/websockets never
    # live). The tool backend re-checks egress + URL before any fetch.
    "browser",
})


def resolve_turn(session_id: str | None, agent_id: str | None = None):
    from app.services import store

    if not session_id:
        raise AccessDenied("An owned session is required")
    parent = current_execution(required=False)
    if parent:
        if parent.session_id != session_id:
            raise AccessDenied("Execution cannot switch sessions")
        return store.access.resolve_context(parent.user_id, session_id, agent_id, parent=parent)
    session = store.get_session(session_id)
    if not session or not session.get("user_id"):
        raise AccessDenied("Session execution identity is unavailable")
    # Non-login channels must explicitly bind a trusted current Admin context at
    # their ingress. In particular, neither web nor tg_* is a privileged default.
    return store.access.resolve_context(session["user_id"], session_id, agent_id)


def authorize_tool(name: str, arguments: dict | None = None):
    """Direct builtin calls and dispatcher calls share auditable admission."""
    from app.runtime.tools.registry import _audit_tool
    try:
        context = _authorize_tool(name, arguments)
    except AccessDenied:
        _audit_tool(name, "denied")
        raise
    _audit_tool(name, "admitted")
    return context


def _swarm_board_grant(context):
    """The swarm runtime grants ``swarm_board`` per worker task at bind time.

    The agent catalog intentionally omits it (see ``get_agent_openai_tools``);
    usability comes only from a bound task scope that already verified the
    run's session ownership. The grant is session-scoped, not agent-scoped:
    schema provisioning happens in the spawning task before the worker turn
    binds its own agent identity, while dispatch still runs under the bound
    task's fences (roster, allowed tools, write scope). Without a matching
    bound scope there is no grant.
    """
    from app.models.mixins import swarm as swarm_store
    from app.runtime.tools.swarm_board import current_scope
    from app.services import store
    scope = current_scope()
    if not scope:
        return None
    run = store.with_db(lambda conn: swarm_store.get_run(conn, scope["run_id"]))
    if not run or run["session_id"] != context.session_id:
        return None
    return context


def _authorize_tool(name: str, arguments: dict | None = None):
    from app.services import store

    current = current_execution()
    if name == "swarm_board":
        granted = _swarm_board_grant(store.access.revalidate(current)) if current else None
        context = granted if granted is not None else store.access.require_tool(current, name)
    elif name.startswith("plugin__"):
        # Plugin tools are enabled per plugin (Admin-managed), not per
        # agent row: the store has no per-agent plugin toggle. The ceiling
        # is the revalidated owned context for Admins; Members additionally
        # need the tool in their explicitly-assigned ceiling (no implicit
        # opt-in to external execution).
        if current.role != "admin":
            context = store.access.require_tool(current, name)
        else:
            context = store.access.revalidate(current)
    elif _is_mcp_tool_name(name):
        # Enablement first so disabled/forged ids report accurately;
        # the ceiling check below still applies to enabled tools.
        _require_mcp_enabled(name)
        context = store.access.require_tool(current, name)
    else:
        context = store.access.require_tool(current, name)
    if name not in RESTRICTED_TOOLS | ADMIN_TOOLS | {"agent_info"} and not (
        _is_mcp_tool_name(name) or name.startswith("plugin__")
    ):
        # Includes network/browser services without an owned supervised
        # boundary. Destination host grants are not their boundary.
        raise AccessUnavailable("Tool service has no owned, supervised execution boundary")
    if context.role != "admin" and (name in ADMIN_TOOLS or name.startswith("plugin__") or _is_mcp_tool_name(name)):
        # Shared-config mutators stay Admin-only. Member-eligible tools
        # (sandboxed external execution, user-scoped skills/state) fall
        # through to the boundary checks below; their backends enforce the
        # sandbox/scope. Members cannot manage either surface (HTTP 403).
        if name not in MEMBER_SANDBOXED_TOOLS and not (
            name.startswith("plugin__") or _is_mcp_tool_name(name)
        ):
            raise AccessDenied("Administrative tools are unavailable")
    if _is_mcp_tool_name(name):
        _require_mcp_enabled(name)
    if name.startswith("plugin__"):
        _require_plugin_enabled(name)
    if name in ("web_search", "web_fetch"):
        from app.runtime.net_policy import require_egress

        require_egress(name)
    if name == "vision_analyze" and isinstance(arguments, dict):
        source = str(arguments.get("source") or arguments.get("image_url") or "").strip()
        if source.startswith(("http://", "https://")):
            from app.runtime.net_policy import require_egress

            require_egress(name)
    if (context.execution_mode == "restricted" or context.role != "admin") and name not in RESTRICTED_TOOLS:
        # Member-eligible tools carry their own boundary (per-chat sandbox
        # execution or user-scoped state); they are never host-executed for
        # Members, in either mode.
        if not (
            context.role != "admin"
            and (name in MEMBER_SANDBOXED_TOOLS
                 or name.startswith("plugin__")
                 or _is_mcp_tool_name(name))
        ):
            raise AccessUnavailable("Tool service has no restricted execution boundary")
    if arguments is not None and not isinstance(arguments, dict):
        raise AccessDenied("Tool arguments must be an object")
    args = arguments or {}
    for key in ("user_id", "owner_user_id"):
        if args.get(key) and args[key] != context.user_id:
            raise AccessDenied("Tool cannot change execution owner")
    if args.get("session_id") and args["session_id"] != context.session_id:
        raise AccessDenied("Tool cannot access another session")
    if args.get("agent_id") and name not in ADMIN_TOOLS | {"delegate"}:
        store.access.resolve_context(context.user_id, context.session_id, args["agent_id"], parent=context)
    hint = args.get("workplace") or (args.get("workplace_id") if name not in ADMIN_TOOLS else None)
    if hint:
        from app.runtime.tools.workplace_ctx import match_workplace
        enabled = {r.workplace_id for r in context.resources}
        candidates = [w for w in store.access.list_visible_workplaces(context.user_id) if w['id'] in enabled]
        selected = match_workplace(candidates, str(hint))
        if not selected:
            raise AccessDenied("Requested working location is unavailable")
        store.access.authorize_resource(context, selected['id'])
        if context.execution_mode == 'unrestricted' and selected['id'] != context.destination_id:
            raise AccessDenied("Unrestricted execution is not activated for this destination")
    if name == "agent_info" and context.role != "admin":
        # The legacy inspector includes shared agent state and host credential
        # metadata. Its account-safe replacement is the live authorized roster.
        raise AccessUnavailable("Detailed shared agent inspection is unavailable")
    return context


def normalize_tool_arguments(context, name: str, arguments: dict) -> dict:
    """Resolve hints only within enabled grants, then send opaque IDs to brokers."""
    from app.services import store
    from app.runtime.tools.workplace_ctx import match_workplace
    if name in ADMIN_TOOLS or not isinstance(arguments, dict):
        return arguments
    result = dict(arguments)
    enabled = {r.workplace_id for r in context.resources}
    for key in ('workplace', 'workplace_id'):
        if result.get(key):
            candidates = [w for w in store.access.list_visible_workplaces(context.user_id) if w['id'] in enabled]
            hit = match_workplace(candidates, str(result[key]))
            if not hit:
                raise AccessDenied("Requested working location is unavailable")
            result[key] = hit['id']
    return result


def tool_available(context, name: str) -> bool:
    if name == "swarm_board" and name not in context.tool_ids:
        # Schema provisioning follows the same per-task runtime grant as
        # dispatch; the catalog gate alone must not hide the bound channel.
        try:
            if _swarm_board_grant(context) is not None:
                return True
        except AccessDenied:
            pass
    if _is_mcp_tool_name(name) or name.startswith("plugin__"):
        if context.role == "admin":
            # Admin external tools: ceiling is the per-agent toggle (MCP rows)
            # or the plugin enablement switch; schemas stay hidden otherwise.
            try:
                if _is_mcp_tool_name(name):
                    _require_mcp_enabled(name)
                else:
                    _require_plugin_enabled(name)
            except AccessDenied:
                return False
            if _is_mcp_tool_name(name) and name not in context.tool_ids:
                return False
            return context.execution_mode != "restricted"
        # Members: currently assigned (ceiling) + enabled; execution is
        # always per-chat sandboxed, in either mode. Unassigned, disabled
        # or forged ids stay hidden (never offered then denied).
        if name not in context.tool_ids:
            return False
        try:
            if _is_mcp_tool_name(name):
                _require_mcp_enabled(name)
            else:
                _require_plugin_enabled(name)
        except AccessDenied:
            return False
        return True
    if context.role != "admin" and name in MEMBER_SANDBOXED_TOOLS:
        # User-scoped skills/state: visible only when assigned to the
        # current agent ceiling; the backends enforce the per-user scope.
        return name in context.tool_ids
    return (name in context.tool_ids
            and name in RESTRICTED_TOOLS | ADMIN_TOOLS | {"agent_info"}
            and not (context.role != "admin" and (name in ADMIN_TOOLS or name.startswith("plugin__") or name == "agent_info"))
            and ((context.execution_mode != "restricted" and context.role == "admin") or name in RESTRICTED_TOOLS))


def filter_schemas(context, schemas: list[dict]) -> list[dict]:
    return [s for s in schemas if tool_available(context, s.get("function", {}).get("name", ""))]


def authorized_agents(context) -> list[dict]:
    from app.services import store

    result = []
    for agent in store.access.list_visible_agents(context.user_id):
        try:
            store.access.resolve_context(context.user_id, context.session_id, agent["id"], parent=context)
        except AccessDenied:
            continue
        result.append(agent)
    return result


def authorize_model_client(client) -> None:
    """Recheck auxiliary as well as main-model assignments before each request."""
    context = current_execution(required=False)
    if context:
        from app.services import store
        context = store.access.revalidate(context)
        profile = getattr(client, "context_profile", None)
        if profile and profile.get("id"):
            store.access.require_use(context.user_id, "model", profile["id"])


def authorize_private_user(user_id: str) -> None:
    """Trusted HTTP primitives may be unbound; a bound runtime may never pivot."""
    context = current_execution(required=False)
    if context:
        from app.services import store
        context = store.access.revalidate(context)
        if context.user_id != user_id:
            raise AccessDenied("Private data is outside the execution owner scope")


def require_owned_service(session_id: str, *, user_id: str | None = None) -> str:
    """Ownership for read/cancel controls does not require an executable model."""
    from app.services import store
    context = current_execution(required=False)
    uid = user_id or (context.user_id if context else None)
    if not uid:
        raise AccessDenied("Caller identity is required")
    store.access.require_user(uid)
    if context and (context.user_id != uid or context.session_id != session_id):
        raise AccessDenied("Session is unavailable")
    session = store.get_session(session_id)
    if not session or session['user_id'] != uid:
        raise AccessDenied("Session is unavailable")
    return uid


def require_session_service(session_id: str, *, user_id: str | None = None):
    """Direct HTTP services require caller identity; tools use the bound ceiling."""
    from app.services import store

    context = current_execution(required=False)
    if context:
        if context.session_id != session_id or (user_id and context.user_id != user_id):
            raise AccessDenied("Session is unavailable")
        return store.access.revalidate(context)
    if not user_id:
        raise AccessDenied("Execution identity is required")
    return store.access.resolve_context(user_id, session_id)


def intersect_context(left, right):
    """Batch continuations inherit the intersection, never the last job's scope."""
    from dataclasses import replace
    from app.services import store

    left, right = store.access.revalidate(left), store.access.revalidate(right)
    identity = ('user_id', 'session_id', 'execution_mode', 'destination_id', 'access_generation')
    if any(getattr(left, key) != getattr(right, key) for key in identity):
        raise AccessDenied("Queued execution ceilings do not match")
    other = {r.workplace_id: r for r in right.resources}
    resources = []
    for resource in left.resources:
        old = other.get(resource.workplace_id)
        if old and (resource.root_path, resource.destination_id) == (old.root_path, old.destination_id):
            resources.append(replace(resource, permission='read_write' if resource.writable and old.writable else 'read'))
    return store.access.revalidate(replace(left, resources=tuple(resources), tool_ids=left.tool_ids & right.tool_ids))


def durable_context(item: dict):
    from app.runtime.access import ExecutionContext
    from app.services import store

    value = item.get("execution_context")
    if not isinstance(value, dict) or not value:
        raise AccessDenied("Stored execution identity is unavailable")
    try:
        context = store.access.revalidate(ExecutionContext.from_dict(value))
    except (TypeError, ValueError, KeyError) as exc:
        raise AccessDenied("Stored execution identity is invalid") from exc
    if item.get("user_id", item.get("owner_user_id")) != context.user_id:
        raise AccessDenied("Stored execution owner is unavailable")
    if item.get("session_id") and item["session_id"] != context.session_id:
        raise AccessDenied("Stored execution session is unavailable")
    return context
