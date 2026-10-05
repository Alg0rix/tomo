"""Platform API — tools, skills, plugins, workplaces, schedules, settings."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.config import EVAL_UI_ENABLED, FS_BROWSE_ROOT
from app.core.deps import AdminDep, AuthDep, authenticated_user, can_manage_telegram, session_user_id
from app.api.access_policy import require_agent
from app.runtime.llm import codex_models, codex_oauth
from app.schemas import (
    CodexLoginPoll,
    LLMProfileCreate,
    LLMProfileUpdate,
    McpItemEnabled,
    McpPromptGet,
    McpResourceRead,
    McpServerCreate,
    McpServerUpdate,
    ScheduleCreate,
    ScheduleUpdate,
    UserCreate,
    UserUpdate,
    ApiKeyCreate,
    WorkplaceCreate,
    WorkplaceInstallViaSsh,
    WorkplaceUpdate,
)
from app.services import store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class EnsureLocalWorkplaceIn(BaseModel):
    """Open-folder style: register (or reuse) a local path as a workplace."""

    path: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=80)


_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "proc",
        "sys",
        "dev",
    }
)


def _resolve_browse_path(raw: str | None) -> Path:
    from app.core.paths import ensure_under

    text = (raw or "").strip() or str(Path.home())
    # Admin FS browse: jail under configurable root (default: home).
    browse_root = FS_BROWSE_ROOT
    try:
        browse_root = browse_root.resolve()
        p = ensure_under(browse_root, Path(text).expanduser())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid path: {exc}") from exc
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"path not found: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {p}")
    return p


def _require_eval_ui() -> None:
    if not EVAL_UI_ENABLED:
        raise HTTPException(status_code=404, detail="Evaluation UI is disabled")


@router.get("/tools")
async def list_tools(_: AuthDep):
    return {"tools": store.list_tools()}


@router.get("/agents/{agent_id}/tools")
async def agent_tools(agent_id: str, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"tools": store.get_agent_tools(agent_id)}


@router.put("/agents/{agent_id}/tools")
async def update_agent_tools(agent_id: str, body: dict, _: AuthDep):
    """Persist per-agent tool enablement.

    Body: ``{"enabled": {"bash": true, "web_fetch": false, ...}}`` or
    ``{"tools": [{"id": "bash", "enabled": true}, ...]}``.
    """
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    enabled: dict[str, bool] = {}
    raw_map = body.get("enabled")
    if isinstance(raw_map, dict):
        for key, val in raw_map.items():
            if isinstance(key, str):
                enabled[key] = bool(val)
    elif isinstance(body.get("tools"), list):
        for row in body["tools"]:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                enabled[row["id"]] = bool(row.get("enabled", False))
    else:
        raise HTTPException(
            status_code=400,
            detail="Body must include 'enabled' map or 'tools' list",
        )
    tools = store.set_agent_tools(agent_id, enabled)
    return {"tools": tools}


@router.get("/skills")
async def list_skills(_: AuthDep):
    try:
        store.sync_skills()
    except Exception:
        pass
    return {"skills": store.list_skills()}


@router.post("/skills/sync")
async def sync_skills(_: AuthDep):
    skills = store.sync_skills()
    return {"skills": skills, "count": len(skills)}


class SkillInstallIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    id: str | None = Field(default=None, max_length=80)


@router.post("/skills/install")
async def install_skill(body: SkillInstallIn, _: AuthDep):
    try:
        skill = store.install_skill_from_path(body.path, skill_id=body.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, _: AuthDep):
    if store.uninstall_library_skill(skill_id):
        return {"ok": True, "removed": "library"}
    if store.delete_skill(skill_id):
        return {"ok": True, "removed": "catalog"}
    raise HTTPException(status_code=404, detail="Skill not found")


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, _: AuthDep):
    skill = store.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    from app.extensions.skills import read_skill_body

    body = read_skill_body(skill_id)
    return {**skill, "body": body or skill.get("description") or ""}


@router.put("/skills/{skill_id}")
async def update_skill(skill_id: str, body: dict, _: AuthDep):
    skill = store.update_skill(skill_id, body)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.get("/workplaces")
async def list_workplaces(request: Request, _: AuthDep):
    user = authenticated_user(request)
    return {"workplaces": store.list_workplaces() if user["role"] == "admin"
            else store.access.list_visible_workplaces(user["id"])}


@router.get("/fs/browse")
async def browse_filesystem(
    _: AuthDep,
    path: str | None = Query(default=None, description="Directory to list"),
    q: str | None = Query(default=None, description="Filter/search dir names"),
    limit: int = Query(default=200, ge=1, le=500),
):
    """List subdirectories for the Open Folder picker (server filesystem)."""
    root = _resolve_browse_path(path)
    needle = (q or "").strip().casefold()
    entries: list[dict] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.casefold())
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"permission denied: {root}")
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for child in children:
        if len(entries) >= limit:
            break
        name = child.name
        if name in _SKIP_DIR_NAMES:
            continue
        # Hide most dot dirs except common project roots
        if name.startswith(".") and name not in {".config", ".local", ".tomo"}:
            if not needle or needle not in name.casefold():
                continue
        if needle and needle not in name.casefold():
            continue
        try:
            if not child.is_dir():
                continue
            resolved = str(child.resolve())
        except OSError:
            continue
        entries.append({"name": name, "path": resolved, "type": "dir"})

    parent = None
    if root.parent != root:
        try:
            parent = str(root.parent.resolve())
        except OSError:
            parent = str(root.parent)

    return {
        "path": str(root),
        "parent": parent,
        "home": str(Path.home().resolve()),
        "entries": entries,
        "capped": len(entries) >= limit,
        "query": needle or "",
    }


@router.post("/workplaces/ensure-local")
async def ensure_local_workplace(body: EnsureLocalWorkplaceIn, _: AuthDep):
    """Create or reuse a local workplace for an absolute path (VS Code open-folder)."""
    from app.core.paths import ensure_under

    browse_root = FS_BROWSE_ROOT
    try:
        browse_root = browse_root.resolve()
        p = ensure_under(browse_root, Path(body.path).expanduser())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid path: {exc}") from exc
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {p}")
    path = str(p)
    name = (body.name or "").strip() or (p.name or "local")
    # Reuse existing local with same root_path.
    for w in store.list_workplaces():
        if (w.get("kind") or "") == "local" and (w.get("root_path") or "") == path:
            return {"workplace": w, "created": False}
    try:
        wp = store.create_workplace(
            {"name": name, "kind": "local", "root_path": path}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"workplace": wp, "created": True}


@router.post("/workplaces")
async def create_workplace(body: WorkplaceCreate, _: AuthDep):
    try:
        return store.create_workplace(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/workplaces/{workplace_id}")
async def get_workplace(workplace_id: str, request: Request, _: AuthDep):
    user = authenticated_user(request)
    if user["role"] != "admin":
        wp = next((w for w in store.access.list_visible_workplaces(user["id"]) if w["id"] == workplace_id), None)
        if not wp:
            raise HTTPException(404, "Workplace not found")
        return wp
    wp = store.get_workplace(workplace_id)
    if not wp:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return wp


@router.put("/workplaces/{workplace_id}")
async def update_workplace(workplace_id: str, body: WorkplaceUpdate, _: AuthDep):
    try:
        wp = await asyncio.to_thread(store.update_workplace, workplace_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not wp:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return wp


@router.delete("/workplaces/{workplace_id}")
async def delete_workplace(workplace_id: str, _: AuthDep):
    if not await asyncio.to_thread(store.delete_workplace, workplace_id):
        raise HTTPException(status_code=404, detail="Workplace not found")
    return {"success": True}


@router.post("/workplaces/{workplace_id}/disable")
async def disable_workplace(workplace_id: str, _: AuthDep):
    try:
        wp = await asyncio.to_thread(store.set_workplace_enabled, workplace_id, False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not wp:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return {"success": True, "workplace": wp}


@router.post("/workplaces/{workplace_id}/enable")
async def enable_workplace(workplace_id: str, _: AuthDep):
    try:
        wp = await asyncio.to_thread(store.set_workplace_enabled, workplace_id, True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not wp:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return {"success": True, "workplace": wp}


@router.post("/workplaces/{workplace_id}/connect")
async def connect_workplace(workplace_id: str, _: AuthDep):
    """Test connection; persist status (tunnel only ``connected`` with live socket)."""
    result = store.connect_workplace(workplace_id)
    if not result:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return result


@router.post("/workplaces/{workplace_id}/pairing-code")
async def issue_pairing_code(workplace_id: str, _: AuthDep):
    """Generate a short-lived pairing code for a tunnel workplace."""
    try:
        wp = store.issue_pairing_code(workplace_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not wp:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return {
        "workplace": wp,
        "pairing_code": wp.get("pairing_code") or "",
        "pairing_expires_at": wp.get("pairing_expires_at") or 0,
        "pairing_ttl_seconds": wp.get("pairing_ttl_seconds") or 0,
    }


@router.post("/workplaces/install-via-ssh")
async def install_via_ssh(body: WorkplaceInstallViaSsh, _: AuthDep):
    """Option C: install the Tomo Connector on a remote host over SSH.

    Downloads the connector binary from GitHub Releases, sets up a systemd
    ``--user`` unit, pairs it, and registers the host as a ``tunnel``
    workplace. Returns the new workplace plus a concatenated install log.
    """
    from app.workplaces.install_via_ssh import InstallError, install_via_ssh as run_install

    try:
        result = run_install(
            ssh_host=body.ssh_host,
            ssh_port=body.ssh_port,
            ssh_user=body.ssh_user,
            ssh_password=body.ssh_password,
            ssh_key=body.ssh_key,
            name=body.name,
            server_url=body.server_url,
            arch=body.arch,
            os_name=body.os_name,
            version=body.version,
            verify=body.verify,
        )
    except InstallError as e:
        raise HTTPException(
            status_code=502,
            detail={"stage": e.stage, "message": str(e), "retryable": e.retryable},
        ) from e
    return {
        "workplace": result.workplace,
        "status": result.status,
        "log": result.log,
        "exit_code": 0,
    }


class ScopedScheduleCreate(ScheduleCreate):
    session_id: str = Field(default="", max_length=80)


def _schedule(request: Request, schedule_id: str) -> dict:
    user = authenticated_user(request)
    from app.runtime.access import AccessDenied
    try:
        return store.access.require_schedule(user["id"], schedule_id)
    except AccessDenied as exc:
        raise HTTPException(404, "Schedule not found") from exc


def _public_schedule(schedule: dict) -> dict:
    return {k: v for k, v in schedule.items() if k != "execution_context"}


def _validate_schedule_execution(request: Request, schedule: dict) -> None:
    from app.runtime.access import ExecutionContext

    if schedule.get("execution_context"):
        store.access.revalidate(ExecutionContext.from_dict(schedule["execution_context"]))
    else:
        raise HTTPException(503, "Schedule execution context unavailable")


@router.get("/schedules")
async def list_schedules(request: Request, _: AuthDep):
    user = authenticated_user(request)
    agents = {a["id"]: a for a in store.access.list_visible_agents(user["id"])}
    rows = []
    schedules = store.access.list_visible_schedules(user["id"])
    for s in schedules:
        row = _public_schedule(s)
        agent = agents.get(s.get("agent_id"))
        row["agent_name"] = agent["name"] if agent else s.get("agent_id")
        rows.append(row)
    return {"schedules": rows}


@router.post("/schedules")
async def create_schedule(body: ScopedScheduleCreate, request: Request, _: AuthDep):
    uid = session_user_id(request)
    require_agent(request, body.agent_id)
    sid = body.session_id
    if not sid:
        sid = store.create_swarm_session([body.agent_id], uid, body.agent_id)
    from app.core.deps import require_owned_session

    require_owned_session(request, sid)
    context = store.access.resolve_context(uid, sid)
    try:
        return _public_schedule(store.access.create_schedule_for_context(
            context, body.model_dump(exclude_none=True, exclude={"session_id"})))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, request: Request, _: AuthDep):
    return _public_schedule(_schedule(request, schedule_id))


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: ScheduleUpdate, request: Request, _: AuthDep):
    existing = _schedule(request, schedule_id)
    data = body.model_dump(exclude_unset=True)
    if body.agent_id and body.agent_id != existing["agent_id"]:
        raise HTTPException(403, "Create a new schedule to change its execution scope")
    if data.get("enabled"):
        _validate_schedule_execution(request, existing)
    try:
        sch = store.update_schedule(schedule_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not sch:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _public_schedule(sch)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, request: Request, _: AuthDep):
    _schedule(request, schedule_id)
    if not store.delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"success": True}


@router.get("/schedules/{schedule_id}/runs")
async def list_schedule_runs(schedule_id: str, request: Request, _: AuthDep):
    _schedule(request, schedule_id)
    return {"runs": store.list_schedule_runs(schedule_id)}


@router.post("/schedules/{schedule_id}/run")
async def run_schedule(schedule_id: str, request: Request, _: AuthDep):
    """Fire a schedule immediately (outside the normal due window)."""
    from app.scheduler.runner import run_schedule_now

    _validate_schedule_execution(request, _schedule(request, schedule_id))
    try:
        result = await run_schedule_now(schedule_id, user_id=session_user_id(request))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/schedules/{schedule_id}/pause")
async def pause_schedule(schedule_id: str, request: Request, _: AuthDep):
    _schedule(request, schedule_id)
    sch = store.pause_schedule(schedule_id)
    if not sch:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _public_schedule(sch)


@router.post("/schedules/{schedule_id}/resume")
async def resume_schedule(schedule_id: str, request: Request, _: AuthDep):
    _validate_schedule_execution(request, _schedule(request, schedule_id))
    try:
        sch = store.resume_schedule(schedule_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not sch:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _public_schedule(sch)


@router.get("/models")
async def list_models(request: Request, _: AuthDep):
    user = authenticated_user(request)
    if user["role"] != "admin":
        return {"models": store.access.list_visible_models(user["id"]), "providers": []}
    return {"models": store.list_models(), "providers": store.list_providers()}


@router.get("/llm-profiles")
async def list_llm_profiles(request: Request, _: AuthDep):
    user = authenticated_user(request)
    if user["role"] != "admin":
        profiles = store.access.list_visible_models(user["id"])
        default_id = store.get_default_llm_profile_id()
        return {"profiles": profiles, "default_id": default_id if any(p["id"] == default_id for p in profiles) else ""}
    return {
        "profiles": store.list_llm_profiles(),
        "default_id": store.get_default_llm_profile_id(),
    }


async def _profile_catalog_data(data: dict, profile_id: str | None = None) -> dict:
    from app.models.mixins.llm_profiles import get_profile
    from app.runtime.llm.provider_catalog import fetch_models, provider_for_url

    existing = store.with_db(lambda conn: get_profile(conn, profile_id)) if profile_id else None
    url = data.get("base_url", (existing or {}).get("base_url", "")) or ""
    provider = provider_for_url(url)
    if provider:
        key = data.get("api_key") or (existing or {}).get("api_key") or ""
        models = await fetch_models(provider, key)
        model = data.get("model", (existing or {}).get("model")) or models[0]
        if model not in models:
            raise ValueError("Choose a model from this provider's catalog")
        data.update(available_models=models, model=model)
    elif "base_url" in data:
        data["available_models"] = []
    return data


class ProviderModelsIn(BaseModel):
    provider: str
    api_key: str = ""
    profile_id: str | None = None


@router.post("/llm-profiles/provider-models")
async def get_provider_models(body: ProviderModelsIn, _: AuthDep):
    from app.models.mixins.llm_profiles import get_profile
    from app.runtime.llm.provider_catalog import fetch_models

    key = body.api_key
    if not key and body.profile_id:
        profile = store.with_db(lambda conn: get_profile(conn, body.profile_id))
        key = (profile or {}).get("api_key") or ""
    try:
        return {"models": await fetch_models(body.provider, key)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/llm-profiles")
async def create_llm_profile(body: LLMProfileCreate, _: AuthDep):
    try:
        data = await _profile_catalog_data(body.model_dump(exclude_none=True))
        profile = store.create_llm_profile(data)
        current = store.get_llm_profile(store.get_default_llm_profile_id())
        # The first usable profile becomes the main model instead of a silent fallback.
        if profile and profile.get("enabled") and not (current and current.get("enabled")):
            store.set_default_llm_profile(profile["id"])
        return profile
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/llm-profiles/chat-options")
async def get_chat_model_options(request: Request, _: AuthDep, agent_id: str | None = None):
    if agent_id:
        require_agent(request, agent_id)
    user = authenticated_user(request)
    if user["role"] != "admin":
        profiles = store.access.list_visible_models(user["id"])
        default_id = store.get_default_llm_profile_id()
        selected_id = (store.get_agent(agent_id) or {}).get("model_id") if agent_id else default_id
        selected = next((p for p in profiles if p["id"] == selected_id), None)
        return {"session_id": "", "profile_id": (selected or {}).get("id", ""),
                "profile_name": (selected or {}).get("name", ""), "model": (selected or {}).get("model", ""),
                "main_model": (selected or {}).get("model", ""), "selected_model_profile_id": "",
                "model_profiles": [{"id": p["id"], "name": p["name"], "models": p["available_models"] or [p["model"]]} for p in profiles],
                "reasoning_efforts": [], "reasoning_effort": None, "selected_reasoning_effort": None,
                "default_reasoning_effort": None}
    return store.get_chat_model_settings(agent_id)


@router.get("/llm-profiles/codex-models")
async def get_codex_models(_: AuthDep, profile_id: str | None = None):
    access_token = store.get_llm_profile_access_token(profile_id) if profile_id else ""
    return {"models": codex_models.list_codex_models(access_token or None)}


@router.get("/llm-profiles/{profile_id}")
async def get_llm_profile(profile_id: str, request: Request, _: AuthDep):
    user = authenticated_user(request)
    if user["role"] != "admin":
        prof = next((p for p in store.access.list_visible_models(user["id"]) if p["id"] == profile_id), None)
        if not prof:
            raise HTTPException(404, "Profile not found")
        return prof
    prof = store.get_llm_profile(profile_id)
    if not prof:
        raise HTTPException(status_code=404, detail="Profile not found")
    return prof


@router.put("/llm-profiles/{profile_id}")
async def update_llm_profile(profile_id: str, body: LLMProfileUpdate, _: AuthDep):
    if not store.get_llm_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    try:
        data = await _profile_catalog_data(body.model_dump(exclude_unset=True), profile_id)
        return store.update_llm_profile(profile_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/llm-profiles/{profile_id}")
async def delete_llm_profile(profile_id: str, _: AuthDep):
    if not store.delete_llm_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"success": True}


class DefaultProfileIn(BaseModel):
    model: str | None = None


@router.post("/llm-profiles/{profile_id}/default")
async def set_default_llm_profile(
    profile_id: str, _: AuthDep, body: DefaultProfileIn | None = None
):
    profile = store.get_llm_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    if body and body.model:
        if body.model not in (profile.get("available_models") or [profile["model"]]):
            raise HTTPException(status_code=400, detail="Model is not in this profile's catalog")
        store.update_llm_profile(profile_id, {"model": body.model})
    store.set_default_llm_profile(profile_id)
    return {"success": True, "default_id": profile_id}


@router.post("/llm-profiles/codex-login/start")
async def codex_login_start(_: AuthDep):
    try:
        return codex_oauth.start_device_login()
    except codex_oauth.CodexAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/llm-profiles/codex-login/poll")
async def codex_login_poll(body: CodexLoginPoll, _: AuthDep):
    try:
        tokens = codex_oauth.poll_device_login(body.device_auth_id, body.user_code)
    except codex_oauth.CodexAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if tokens is None:
        return {"status": "pending"}

    existing = store.find_subscription_llm_profile("openai-codex")
    if existing:
        store.save_subscription_llm_tokens(
            existing["id"],
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=tokens["expires_at"],
        )
        profile = store.get_llm_profile(existing["id"])
    else:
        profile = store.create_subscription_llm_profile(
            provider="openai-codex",
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=tokens["expires_at"],
            name="ChatGPT (Codex)",
            model="gpt-5-codex",
            base_url=codex_oauth.DEFAULT_CODEX_BASE_URL,
        )
        if not store.get_default_llm_profile_id():
            store.set_default_llm_profile(profile["id"])
    return {"status": "ok", "profile": profile}


# -- MCP servers -----------------------------------------------------------


def _mcp_server_or_404(server_id: str) -> dict:
    server = store.get_mcp_server(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return server


@router.get("/mcp-servers")
async def list_mcp_servers(_: AuthDep):
    return {"servers": store.list_mcp_servers()}


@router.post("/mcp-servers")
async def create_mcp_server(body: McpServerCreate, _: AuthDep):
    from app.runtime.mcp import mcp_manager

    try:
        server = store.create_mcp_server(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if server["enabled"]:
        server = await mcp_manager.connect_and_discover(server["id"])
    return server


@router.get("/mcp-servers/{server_id}")
async def get_mcp_server(server_id: str, _: AuthDep):
    server = _mcp_server_or_404(server_id)
    return {**server, "items": store.list_mcp_items(server_id)}


@router.put("/mcp-servers/{server_id}")
async def update_mcp_server(server_id: str, body: McpServerUpdate, _: AuthDep):
    from app.runtime.mcp import mcp_manager

    _mcp_server_or_404(server_id)
    try:
        server = store.update_mcp_server(server_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    if server["enabled"]:
        server = await mcp_manager.connect_and_discover(server_id)
    else:
        await mcp_manager.close_server(server_id)
        server = store.set_mcp_status(server_id, "disabled", "server is disabled")
    return server


@router.delete("/mcp-servers/{server_id}")
async def delete_mcp_server(server_id: str, _: AuthDep):
    from app.runtime.mcp import mcp_manager

    _mcp_server_or_404(server_id)
    await mcp_manager.close_server(server_id)
    store.delete_mcp_server(server_id)
    return {"success": True}


@router.post("/mcp-servers/{server_id}/refresh")
async def refresh_mcp_server(server_id: str, _: AuthDep):
    from app.runtime.mcp import mcp_manager

    server = _mcp_server_or_404(server_id)
    if not server["enabled"]:
        raise HTTPException(status_code=409, detail="MCP server is disabled")
    # Force a fresh connect + full re-discovery rather than reusing any live session.
    await mcp_manager.close_server(server_id)
    return await mcp_manager.connect_and_discover(server_id)


@router.put("/mcp-servers/{server_id}/items/{item_id}")
async def set_mcp_item_enabled(server_id: str, item_id: str, body: McpItemEnabled, _: AuthDep):
    server = _mcp_server_or_404(server_id)
    item = store.get_mcp_item(item_id)
    if not item or item["server_id"] != server_id:
        raise HTTPException(status_code=404, detail="MCP item not found")
    if not server["enabled"]:
        raise HTTPException(status_code=409, detail="MCP server is disabled")
    updated = store.set_mcp_item_enabled(item_id, body.enabled)
    return updated


@router.get("/mcp-servers/{server_id}/resources")
async def list_mcp_resources(server_id: str, _: AuthDep):
    _mcp_server_or_404(server_id)
    return {
        "resources": store.list_mcp_items(server_id, kind="resource")
        + store.list_mcp_items(server_id, kind="resource_template"),
    }


def _find_enabled_item(server_id: str, *, kind: str, match: str, by: str) -> dict:
    for item in store.list_mcp_items(server_id, kind=kind):
        if item.get(by) == match:
            if not item["enabled"]:
                raise HTTPException(status_code=409, detail=f"MCP {kind} is disabled")
            return item
    raise HTTPException(status_code=404, detail=f"MCP {kind} not found")


@router.post("/mcp-servers/{server_id}/resources/read")
async def read_mcp_resource(server_id: str, body: McpResourceRead, _: AuthDep):
    from app.runtime.mcp import mcp_manager

    server = _mcp_server_or_404(server_id)
    if not server["enabled"]:
        raise HTTPException(status_code=409, detail="MCP server is disabled")
    _find_enabled_item(server_id, kind="resource", match=body.uri, by="uri")
    try:
        return await mcp_manager.read_resource(server_id, body.uri)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/mcp-servers/{server_id}/prompts")
async def list_mcp_prompts(server_id: str, _: AuthDep):
    _mcp_server_or_404(server_id)
    return {"prompts": store.list_mcp_items(server_id, kind="prompt")}


@router.post("/mcp-servers/{server_id}/prompts/get")
async def get_mcp_prompt(server_id: str, body: McpPromptGet, _: AuthDep):
    from app.runtime.mcp import mcp_manager

    server = _mcp_server_or_404(server_id)
    if not server["enabled"]:
        raise HTTPException(status_code=409, detail="MCP server is disabled")
    _find_enabled_item(server_id, kind="prompt", match=body.name, by="name")
    try:
        return await mcp_manager.get_prompt(server_id, body.name, body.arguments)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/users")
async def list_users(_: AuthDep):
    return {"users": store.list_users()}


@router.post("/users")
async def create_user(body: UserCreate, request: Request, _: AuthDep):
    try:
        return store.access.create_account(session_user_id(request), body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users/{user_id}")
async def get_user(user_id: str, _: AuthDep):
    user = store.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.put("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdate, request: Request, _: AuthDep):
    data = body.model_dump(exclude_unset=True)
    if "password" in data and not data["password"]:
        data.pop("password", None)
    try:
        user = await asyncio.to_thread(store.access.update_account, session_user_id(request), user_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, request: Request, _: AuthDep):
    if user_id == session_user_id(request):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    try:
        ok = await asyncio.to_thread(store.access.delete_account, session_user_id(request), user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True}


def _require_link_account(request: Request, user_id: str) -> None:
    if session_user_id(request) != user_id and not can_manage_telegram(request):
        raise HTTPException(status_code=403, detail="Cannot manage another account's Telegram links")
    if not store.get_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")


@router.get("/users/{user_id}/telegram")
async def list_telegram_links(user_id: str, request: Request, _: AuthDep):
    _require_link_account(request, user_id)
    from app.services.telegram_accounts import list_links

    return {"links": list_links(user_id)}


@router.post("/users/{user_id}/telegram/link-code")
async def create_telegram_link_code(user_id: str, request: Request, _: AuthDep):
    _require_link_account(request, user_id)
    from app.services.telegram_accounts import create_code

    try:
        return create_code(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}/telegram/{chat_id}")
async def unlink_telegram(user_id: str, chat_id: str, request: Request, _: AuthDep):
    _require_link_account(request, user_id)
    from app.services.telegram_accounts import unlink

    try:
        removed = unlink(user_id, chat_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Telegram link not found")
    return {"success": True}


@router.get("/api-keys")
async def list_api_keys(request: Request, _: AuthDep, user_id: str | None = None):
    user = authenticated_user(request)
    if user["role"] == "admin":
        return {"keys": store.list_api_keys(user_id)}
    if user_id and user_id != user["id"]:
        raise HTTPException(403, "Cannot manage another account's keys")
    return {"keys": store.access.list_personal_api_keys(user["id"])}


@router.post("/api-keys")
async def create_api_key(body: ApiKeyCreate, request: Request, _: AuthDep):
    user = authenticated_user(request)
    if user["role"] != "admin" and body.user_id != user["id"]:
        raise HTTPException(403, "Cannot manage another account's keys")
    try:
        if body.user_id == user["id"]:
            return store.access.create_personal_api_key(user["id"], body.name)
        return store.create_api_key(body.user_id, body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api-keys/{key_id}")
async def delete_api_key(key_id: str, request: Request, _: AuthDep):
    user = authenticated_user(request)
    if user["role"] != "admin":
        row = store.get_api_key(key_id)
        if not row or row["user_id"] != user["id"]:
            raise HTTPException(404, "API key not found")
        store.access.delete_personal_api_key(user["id"], key_id)
        return {"success": True}
    if not store.delete_api_key(key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"success": True}


@router.get("/settings")
async def get_settings(_: AuthDep):
    return store.get_public_settings()


@router.put("/settings")
async def update_settings(body: dict, request: Request, _: AuthDep):
    if any(key.startswith("telegram_") for key in body) and not can_manage_telegram(request):
        raise HTTPException(403, "Only administrators can configure Telegram")
    if "memory_consolidation_cron" in body:
        from apscheduler.triggers.cron import CronTrigger

        try:
            CronTrigger.from_crontab(str(body["memory_consolidation_cron"]))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"Invalid consolidation cron: {exc}")
    try:
        result = store.update_settings(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if {"memory_consolidation_enabled", "memory_consolidation_cron"} & body.keys():
        from app.scheduler.engine import _register_builtins

        _register_builtins()
    return result


@router.post("/setup")
async def complete_setup(request: Request, body: dict, _: AdminDep):
    # Bootstrap already seeds an Admin login. Loopback/proxy reachability must
    # never let an anonymous caller or sandbox configure privileged providers.
    if store.is_setup_complete():
        raise HTTPException(status_code=403, detail="Setup already complete")
    base_url = (body.get("base_url") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()
    if base_url and model:
        store.setup_default_profile(
            base_url=base_url,
            api_key=api_key,
            model=model,
            name=(body.get("name") or "Default"),
        )
    return store.update_settings({"setup_complete": True})


@router.get("/agents/{agent_id}/skills")
async def agent_skills(agent_id: str, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"skills": store.get_agent_skills(agent_id)}


@router.put("/agents/{agent_id}/skills")
async def update_agent_skills(agent_id: str, body: dict, _: AuthDep):
    """Persist per-agent skill assignment.

    Body: ``{"skill_ids": ["onboarding", "deploy"]}`` or
    ``{"skills": [{"id": "onboarding", "assigned": true}, ...]}``.
    """
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    skill_ids: list[str] = []
    if isinstance(body.get("skill_ids"), list):
        skill_ids = [str(x) for x in body["skill_ids"] if x]
    elif isinstance(body.get("skills"), list):
        for row in body["skills"]:
            if isinstance(row, dict) and row.get("assigned") and row.get("id"):
                skill_ids.append(str(row["id"]))
    else:
        raise HTTPException(
            status_code=400,
            detail="Body must include 'skill_ids' list or 'skills' list",
        )
    skills = store.set_agent_skills(agent_id, skill_ids)
    return {"skills": skills}


@router.get("/agents/{agent_id}/channels")
async def agent_channels(agent_id: str, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"channels": store.get_agent_channels(agent_id)}


@router.get("/eval/domains")
async def eval_domains(_: AuthDep):
    _require_eval_ui()
    return {"domains": store.list_eval_domains()}


@router.get("/eval/evaluators")
async def eval_evaluators(_: AuthDep):
    _require_eval_ui()
    return {"evaluators": store.list_evaluators()}


@router.get("/eval/runs")
async def eval_runs(_: AuthDep):
    _require_eval_ui()
    return {"runs": store.list_eval_runs()}


@router.get("/eval/runs/{run_id}")
async def eval_run_detail(run_id: str, _: AuthDep):
    _require_eval_ui()
    run = store.get_eval_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run
