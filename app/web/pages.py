"""HTML page routes — server-rendered Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import BRAND, EVAL_UI_ENABLED
from app.core.deps import AuthDep, templates
from app.services import store

router = APIRouter()


def _ctx(request: Request, page: str, **extra):
    return {"page": page, "brand": BRAND, **extra}


def _eval_disabled_redirect() -> RedirectResponse | None:
    if EVAL_UI_ENABLED:
        return None
    return RedirectResponse("/", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _: AuthDep):
    if not store.is_setup_complete():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "index.html", _ctx(request, "dashboard"))


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "agents.html", _ctx(
        request, "agents", agents=store.list_agents(), workplaces=store.list_workplaces(),
        llm_profiles=store.list_llm_profiles(),
    ))


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail_page(request: Request, agent_id: str, _: AuthDep):
    agent = store.get_agent(agent_id)
    if not agent:
        return templates.TemplateResponse(request, "error.html", _ctx(
            request, "error", code=404, message=f"Agent “{agent_id}” not found.",
        ), status_code=404)
    history = store.get_history(agent_id, "web")
    session_id = store.get_or_create_session(agent_id, "web")
    return templates.TemplateResponse(request, "agent_detail.html", _ctx(
        request, "agent", agent=agent, history=history, session_id=session_id,
        tools=store.get_agent_tools(agent_id),
        skills=store.get_agent_skills(agent_id),
        channels=store.get_agent_channels(agent_id),
        llm_profiles=store.list_llm_profiles(),
        workplaces=store.list_workplaces(),
    ))


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "sessions.html", _ctx(
        request, "sessions", agents_list=store.list_agents(),
    ))


@router.get("/workplaces", response_class=HTMLResponse)
async def workplaces_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "workplaces.html", _ctx(
        request, "workplaces", workplaces=store.list_workplaces(),
    ))


@router.get("/workplaces/{workplace_id}", response_class=HTMLResponse)
async def workplace_detail_page(request: Request, workplace_id: str, _: AuthDep):
    wp = store.get_workplace(workplace_id)
    if not wp:
        return templates.TemplateResponse(request, "error.html", _ctx(
            request, "error", code=404, message=f"Workplace “{workplace_id}” not found.",
        ), status_code=404)
    return templates.TemplateResponse(request, "workplace_detail.html", _ctx(
        request, "workplace", workplace=wp, agents=store.list_agents(),
    ))


@router.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "skills.html", _ctx(
        request, "skills", skills=store.list_skills(),
    ))


@router.get("/skills/{skill_id}", response_class=HTMLResponse)
async def skill_detail_page(request: Request, skill_id: str, _: AuthDep):
    skill = store.get_skill(skill_id)
    if not skill:
        return templates.TemplateResponse(request, "error.html", _ctx(
            request, "error", code=404, message=f"Skill “{skill_id}” not found.",
        ), status_code=404)
    return templates.TemplateResponse(request, "skill_detail.html", _ctx(request, "skill", skill=skill))


@router.get("/plugins", response_class=HTMLResponse)
async def plugins_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "plugins.html", _ctx(
        request, "plugins", plugins=store.list_plugins(),
    ))


@router.get("/board", response_class=HTMLResponse)
async def board_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "board.html", _ctx(request, "board"))


@router.get("/usage", response_class=HTMLResponse)
async def usage_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "usage.html", _ctx(request, "usage"))


@router.get("/scheduler", response_class=HTMLResponse)
async def scheduler_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "scheduler.html", _ctx(
        request, "scheduler", schedules=store.list_schedules(), agents=store.list_agents(),
    ))


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "system.html", _ctx(
        request, "system",
        settings=store.get_public_settings(),
        tools=store.list_tools(),
        llm_profiles=store.list_llm_profiles(),
        default_model_id=store.get_default_llm_profile_id(),
        plugins=store.list_plugins(),
        shared_channels=store.list_shared_channels(),
    ))


@router.get("/evaluate", response_class=HTMLResponse)
async def evaluate_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "evaluate.html", _ctx(
        request, "evaluate", eval_page="runner",
        domains=store.list_eval_domains(), models=store.list_models(),
    ))


@router.get("/evaluate/domains", response_class=HTMLResponse)
async def evaluate_domains_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "evaluate_domains.html", _ctx(
        request, "evaluate", eval_page="domains", domains=store.list_eval_domains(),
    ))


@router.get("/evaluate/evaluators", response_class=HTMLResponse)
async def evaluate_evaluators_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "evaluate_evaluators.html", _ctx(
        request, "evaluate", eval_page="evaluators", evaluators=store.list_evaluators(),
    ))


@router.get("/evaluate/settings", response_class=HTMLResponse)
async def evaluate_settings_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "evaluate_settings.html", _ctx(
        request, "evaluate", eval_page="settings", settings=store.get_public_settings(),
    ))


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "history.html", _ctx(
        request, "evaluate", eval_page="history", runs=store.list_eval_runs(),
    ))


@router.get("/history/{run_id}", response_class=HTMLResponse)
async def history_detail_page(request: Request, run_id: str, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    run = store.get_eval_run(run_id)
    if not run:
        return templates.TemplateResponse(request, "error.html", _ctx(
            request, "error", code=404, message=f"Run “{run_id}” not found.",
        ), status_code=404)
    return templates.TemplateResponse(request, "history_detail.html", _ctx(
        request, "evaluate", eval_page="history", run=run,
    ))


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if request.session.get("auth") and store.is_setup_complete():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "setup.html", _ctx(request, "setup"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("auth"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, "login", error=None))


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
