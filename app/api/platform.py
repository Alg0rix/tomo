"""Platform API — tools, skills, plugins, workplaces, schedules, settings."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import AuthDep
from app.services import store

router = APIRouter(prefix="/api")


@router.get("/tools")
async def list_tools(_: AuthDep):
    return {"tools": store.list_tools()}


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


@router.get("/workplaces/{workplace_id}")
async def get_workplace(workplace_id: str, _: AuthDep):
    wp = store.get_workplace(workplace_id)
    if not wp:
        raise HTTPException(status_code=404, detail="Workplace not found")
    return wp


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


@router.get("/settings")
async def get_settings(_: AuthDep):
    return store.get_public_settings()


@router.put("/settings")
async def update_settings(body: dict, _: AuthDep):
    return store.update_settings(body)


@router.post("/setup")
async def complete_setup(body: dict):
    if store.is_setup_complete():
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Setup already complete")
    return store.update_settings({**body, "setup_complete": True})


@router.get("/agents/{agent_id}/tools")
async def agent_tools(agent_id: str, _: AuthDep):
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"tools": store.get_agent_tools(agent_id)}


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
    return {"domains": store.list_eval_domains()}


@router.get("/eval/evaluators")
async def eval_evaluators(_: AuthDep):
    return {"evaluators": store.list_evaluators()}


@router.get("/eval/runs")
async def eval_runs(_: AuthDep):
    return {"runs": store.list_eval_runs()}


@router.get("/eval/runs/{run_id}")
async def eval_run_detail(run_id: str, _: AuthDep):
    run = store.get_eval_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run
