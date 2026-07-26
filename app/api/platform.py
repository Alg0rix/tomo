"""Platform API — tools, skills, plugins, workplaces, schedules, settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.config import EVAL_UI_ENABLED
from app.core.deps import AuthDep
from app.schemas import LLMProfileCreate, LLMProfileUpdate, WorkplaceCreate, WorkplaceUpdate
from app.services import store

router = APIRouter(prefix="/api")


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

    Body: ``{"enabled": {"bash": true, "calculator": false, ...}}`` or
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


@router.get("/workplaces")
async def list_workplaces(_: AuthDep):
    return {"workplaces": store.list_workplaces()}


@router.post("/workplaces")
async def create_workplace(body: WorkplaceCreate, _: AuthDep):
    try:
        return store.create_workplace(body.model_dump())
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
    """Test connection; persist status (tunnel stays ``later``, never fake-connected)."""
    result = store.connect_workplace(workplace_id)
    if not result:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return result


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


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, _: AuthDep):
    sch = store.get_schedule(schedule_id)
    if not sch:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return sch


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
        return store.create_llm_profile(body.model_dump())
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
