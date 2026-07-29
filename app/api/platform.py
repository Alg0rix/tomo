"""Platform API — tools, skills, plugins, workplaces, schedules, settings."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import EVAL_UI_ENABLED
from app.core.deps import AuthDep
from app.schemas import (
    KnowledgeEntryCreate,
    KnowledgeEntryUpdate,
    LLMProfileCreate,
    LLMProfileUpdate,
    ScheduleCreate,
    ScheduleUpdate,
    WorkplaceCreate,
    WorkplaceUpdate,
)
from app.services import store

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
    text = (raw or "").strip() or str(Path.home())
    try:
        p = Path(text).expanduser().resolve()
    except OSError as exc:
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
    return {"skills": store.list_skills()}


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, _: AuthDep):
    skill = store.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.get("/plugins")
async def list_plugins(_: AuthDep):
    return {"plugins": store.list_plugins()}


@router.get("/plugins/{plugin_id}")
async def get_plugin(plugin_id: str, _: AuthDep):
    plugin = store.get_plugin(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return plugin


@router.put("/plugins/{plugin_id}")
async def update_plugin(plugin_id: str, body: dict, _: AuthDep):
    plugin = store.update_plugin(plugin_id, body)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return plugin


@router.put("/skills/{skill_id}")
async def update_skill(skill_id: str, body: dict, _: AuthDep):
    skill = store.update_skill(skill_id, body)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.get("/workplaces")
async def list_workplaces(_: AuthDep):
    return {"workplaces": store.list_workplaces()}


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
        if not child.is_dir():
            continue
        if needle and needle not in name.casefold():
            continue
        try:
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
    try:
        p = Path(body.path).expanduser().resolve()
    except OSError as exc:
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
async def get_workplace(workplace_id: str, _: AuthDep):
    wp = store.get_workplace(workplace_id)
    if not wp:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return wp


@router.put("/workplaces/{workplace_id}")
async def update_workplace(workplace_id: str, body: WorkplaceUpdate, _: AuthDep):
    try:
        wp = store.update_workplace(workplace_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not wp:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return wp


@router.delete("/workplaces/{workplace_id}")
async def delete_workplace(workplace_id: str, _: AuthDep):
    if not store.delete_workplace(workplace_id):
        raise HTTPException(status_code=404, detail="Workplace not found")
    return {"success": True}


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


@router.get("/knowledge")
async def list_knowledge(_: AuthDep):
    return {"entries": store.list_knowledge_entries()}


@router.post("/knowledge")
async def create_knowledge(body: KnowledgeEntryCreate, _: AuthDep):
    try:
        data = body.model_dump(exclude_none=True)
        return store.create_knowledge_entry(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/knowledge/{entry_id}")
async def get_knowledge(entry_id: str, _: AuthDep):
    entry = store.get_knowledge_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return entry


@router.put("/knowledge/{entry_id}")
async def update_knowledge(entry_id: str, body: KnowledgeEntryUpdate, _: AuthDep):
    try:
        entry = store.update_knowledge_entry(
            entry_id, body.model_dump(exclude_unset=True)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not entry:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return entry


@router.delete("/knowledge/{entry_id}")
async def delete_knowledge(entry_id: str, _: AuthDep):
    if not store.delete_knowledge_entry(entry_id):
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return {"success": True}


@router.get("/schedules")
async def list_schedules(_: AuthDep):
    agents = {a["id"]: a for a in store.list_agents()}
    rows = []
    for s in store.list_schedules():
        row = dict(s)
        agent = agents.get(s.get("agent_id"))
        row["agent_name"] = agent["name"] if agent else s.get("agent_id")
        rows.append(row)
    return {"schedules": rows}


@router.post("/schedules")
async def create_schedule(body: ScheduleCreate, _: AuthDep):
    try:
        return store.create_schedule(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, _: AuthDep):
    sch = store.get_schedule(schedule_id)
    if not sch:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return sch


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, body: ScheduleUpdate, _: AuthDep):
    try:
        sch = store.update_schedule(schedule_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not sch:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return sch


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, _: AuthDep):
    if not store.delete_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"success": True}


@router.get("/schedules/{schedule_id}/runs")
async def list_schedule_runs(schedule_id: str, _: AuthDep):
    if not store.get_schedule(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"runs": store.list_schedule_runs(schedule_id)}


@router.get("/models")
async def list_models(_: AuthDep):
    return {"models": store.list_models(), "providers": store.list_providers()}


@router.get("/llm-profiles")
async def list_llm_profiles(_: AuthDep):
    return {
        "profiles": store.list_llm_profiles(),
        "default_id": store.get_default_llm_profile_id(),
    }


@router.post("/llm-profiles")
async def create_llm_profile(body: LLMProfileCreate, _: AuthDep):
    try:
        return store.create_llm_profile(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/llm-profiles/{profile_id}")
async def get_llm_profile(profile_id: str, _: AuthDep):
    prof = store.get_llm_profile(profile_id)
    if not prof:
        raise HTTPException(status_code=404, detail="Profile not found")
    return prof


@router.put("/llm-profiles/{profile_id}")
async def update_llm_profile(profile_id: str, body: LLMProfileUpdate, _: AuthDep):
    prof = store.update_llm_profile(profile_id, body.model_dump(exclude_unset=True))
    if not prof:
        raise HTTPException(status_code=404, detail="Profile not found")
    return prof


@router.delete("/llm-profiles/{profile_id}")
async def delete_llm_profile(profile_id: str, _: AuthDep):
    if not store.delete_llm_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"success": True}


@router.post("/llm-profiles/{profile_id}/default")
async def set_default_llm_profile(profile_id: str, _: AuthDep):
    if not store.get_llm_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found")
    store.set_default_llm_profile(profile_id)
    return {"success": True, "default_id": profile_id}


@router.get("/settings")
async def get_settings(_: AuthDep):
    return store.get_public_settings()


@router.put("/settings")
async def update_settings(body: dict, _: AuthDep):
    return store.update_settings(body)


@router.post("/setup")
async def complete_setup(body: dict):
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
