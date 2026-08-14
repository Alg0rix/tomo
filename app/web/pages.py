"""HTML page routes — server-rendered Jinja2 templates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import EVAL_UI_ENABLED
from app.core.deps import AuthDep, require_owned_session, session_user_id, templates
from app.services import store
from app.web.context import page_ctx

router = APIRouter()


def _eval_disabled_redirect() -> RedirectResponse | None:
    if EVAL_UI_ENABLED:
        return None
    return RedirectResponse("/", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _: AuthDep):
    if not store.is_setup_complete():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "index.html", page_ctx(request, "dashboard"))


@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "agents.html", page_ctx(
        request, "agents", agents=store.list_agents(), workplaces=store.list_workplaces(),
        llm_profiles=store.list_llm_profiles(),
    ))


@router.get("/agents/{agent_id}", response_class=HTMLResponse)
async def agent_detail_page(request: Request, agent_id: str, _: AuthDep):
    agent = store.get_agent(agent_id)
    if not agent:
        return templates.TemplateResponse(request, "error.html", page_ctx(
            request, "error", code=404, message=f"Agent “{agent_id}” not found.",
        ), status_code=404)
    uid = session_user_id(request)
    history = store.get_history(agent_id, uid)
    session_id = store.get_or_create_session(agent_id, uid)
    return templates.TemplateResponse(request, "agent_detail.html", page_ctx(
        request, "agent", agent=agent, history=history, session_id=session_id,
        tools=store.get_agent_tools(agent_id),
        skills=store.get_agent_skills(agent_id),
        channels=store.get_agent_channels(agent_id),
        llm_profiles=store.list_llm_profiles(),
        workplaces=store.list_workplaces(),
    ))


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "sessions.html", page_ctx(
        request, "sessions", agents_list=store.list_agents(),
    ))


@router.get("/companion", response_class=HTMLResponse)
async def companion_page(request: Request, _: AuthDep):
    """Companion — bond, growth log, and what Tomo has learned."""
    return templates.TemplateResponse(
        request, "companion.html", page_ctx(request, "companion")
    )


@router.get("/sessions/{session_id}/artifacts/{filename}/view", response_class=HTMLResponse)
async def artifact_view_page(request: Request, session_id: str, filename: str, _: AuthDep):
    """Full-page artifact viewer (HTML/media chrome-light; text via artifacts.js)."""
    try:
        require_owned_session(request, session_id)
    except HTTPException:
        return templates.TemplateResponse(
            request,
            "error.html",
            page_ctx(request, "error", code=404, message="Session not found."),
            status_code=404,
        )
    from app.runtime.artifacts.fs import (
        artifact_public_url,
        artifacts_dir,
        category_for,
        validate_filename,
    )

    err = validate_filename(filename)
    if err:
        return templates.TemplateResponse(
            request,
            "error.html",
            page_ctx(request, "error", code=400, message=err),
            status_code=400,
        )
    base = artifacts_dir(session_id).resolve()
    path = (base / filename).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "error.html",
            page_ctx(request, "error", code=400, message="Invalid artifact path."),
            status_code=400,
        )
    if not path.is_file():
        return templates.TemplateResponse(
            request,
            "error.html",
            page_ctx(request, "error", code=404, message=f"Artifact “{filename}” not found."),
            status_code=404,
        )
    category = category_for(filename)
    title = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip() or filename
    title = " ".join(w[:1].upper() + w[1:] if w else w for w in title.split())
    return templates.TemplateResponse(
        request,
        "artifact_view.html",
        page_ctx(
            request,
            "artifact_view",
            session_id=session_id,
            filename=filename,
            title=title,
            category=category,
            file_url=artifact_public_url(session_id, filename),
            is_public_view=False,
        ),
    )


@router.get("/share/{token}", response_class=HTMLResponse)
async def shared_artifact_view_page(request: Request, token: str):
    """Public artifact viewer for anyone with a share link."""
    share = store.get_artifact_share(token)
    if not share:
        return templates.TemplateResponse(
            request,
            "error.html",
            page_ctx(request, "error", code=404, message="Share link not found."),
            status_code=404,
        )
    session_id = share["session_id"]
    filename = share["filename"]
    from app.runtime.artifacts.fs import (
        artifacts_dir,
        category_for,
        validate_filename,
    )

    err = validate_filename(filename)
    if err:
        return templates.TemplateResponse(
            request,
            "error.html",
            page_ctx(request, "error", code=400, message=err),
            status_code=400,
        )
    base = artifacts_dir(session_id).resolve()
    path = (base / filename).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "error.html",
            page_ctx(request, "error", code=400, message="Invalid artifact path."),
            status_code=400,
        )
    if not path.is_file():
        return templates.TemplateResponse(
            request,
            "error.html",
            page_ctx(
                request,
                "error",
                code=404,
                message=f"Artifact “{filename}” not found.",
            ),
            status_code=404,
        )
    category = category_for(filename)
    title = filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip() or filename
    title = " ".join(w[:1].upper() + w[1:] if w else w for w in title.split())
    return templates.TemplateResponse(
        request,
        "artifact_view.html",
        page_ctx(
            request,
            "artifact_view",
            session_id=session_id,
            filename=filename,
            title=title,
            category=category,
            file_url=f"/api/share/{token}/raw",
            share_token=token,
            is_public_view=True,
        ),
    )


@router.get("/workplaces", response_class=HTMLResponse)
async def workplaces_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "workplaces.html", page_ctx(
        request, "workplaces", workplaces=store.list_workplaces(),
    ))


@router.get("/workplaces/{workplace_id}", response_class=HTMLResponse)
async def workplace_detail_page(request: Request, workplace_id: str, _: AuthDep):
    wp = store.get_workplace(workplace_id)
    if not wp:
        return templates.TemplateResponse(request, "error.html", page_ctx(
            request, "error", code=404, message=f"Workplace “{workplace_id}” not found.",
        ), status_code=404)
    return templates.TemplateResponse(request, "workplace_detail.html", page_ctx(
        request, "workplace", workplace=wp, agents=store.list_agents(),
    ))


@router.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "skills.html", page_ctx(
        request, "skills", skills=store.list_skills(),
    ))


@router.get("/skills/{skill_id}", response_class=HTMLResponse)
async def skill_detail_page(request: Request, skill_id: str, _: AuthDep):
    skill = store.get_skill(skill_id)
    if not skill:
        return templates.TemplateResponse(request, "error.html", page_ctx(
            request, "error", code=404, message=f"Skill “{skill_id}” not found.",
        ), status_code=404)
    from app.extensions.skills import list_skill_support_files, read_skill_body

    body = read_skill_body(skill_id) or skill.get("description") or ""
    support = list_skill_support_files(skill_id)
    return templates.TemplateResponse(
        request,
        "skill_detail.html",
        page_ctx(
            request,
            "skill",
            skill=skill,
            skill_body=body,
            skill_support=support,
        ),
    )


@router.get("/modules", response_class=HTMLResponse)
async def modules_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "modules.html", page_ctx(
        request, "modules", modules=store.list_modules(),
    ))


@router.get("/plugins", response_class=HTMLResponse)
async def plugins_page_redirect(_: AuthDep):
    return RedirectResponse("/modules", status_code=303)


@router.get("/scheduler", response_class=HTMLResponse)
async def scheduler_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "scheduler.html", page_ctx(
        request, "scheduler", schedules=store.list_schedules(), agents=store.list_agents(),
    ))


@router.get("/system", response_class=HTMLResponse)
async def system_page(request: Request, _: AuthDep):
    return templates.TemplateResponse(request, "system.html", page_ctx(
        request, "system",
        settings=store.get_public_settings(),
        tools=store.list_tools(),
        llm_profiles=store.list_llm_profiles(),
        default_model_id=store.get_default_llm_profile_id(),
        mcp_servers=store.list_mcp_servers(),
        modules=store.list_modules(),
        shared_channels=store.list_shared_channels(),
        users=store.list_users(),
    ))


@router.get("/evaluate", response_class=HTMLResponse)
async def evaluate_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "evaluate.html", page_ctx(
        request, "evaluate", eval_page="runner",
        domains=store.list_eval_domains(), models=store.list_models(),
    ))


@router.get("/evaluate/domains", response_class=HTMLResponse)
async def evaluate_domains_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "evaluate_domains.html", page_ctx(
        request, "evaluate", eval_page="domains", domains=store.list_eval_domains(),
    ))


@router.get("/evaluate/evaluators", response_class=HTMLResponse)
async def evaluate_evaluators_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "evaluate_evaluators.html", page_ctx(
        request, "evaluate", eval_page="evaluators", evaluators=store.list_evaluators(),
    ))


@router.get("/evaluate/settings", response_class=HTMLResponse)
async def evaluate_settings_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "evaluate_settings.html", page_ctx(
        request, "evaluate", eval_page="settings", settings=store.get_public_settings(),
    ))


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    return templates.TemplateResponse(request, "history.html", page_ctx(
        request, "evaluate", eval_page="history", runs=store.list_eval_runs(),
    ))


@router.get("/history/{run_id}", response_class=HTMLResponse)
async def history_detail_page(request: Request, run_id: str, _: AuthDep):
    if (redir := _eval_disabled_redirect()) is not None:
        return redir
    run = store.get_eval_run(run_id)
    if not run:
        return templates.TemplateResponse(request, "error.html", page_ctx(
            request, "error", code=404, message=f"Run “{run_id}” not found.",
        ), status_code=404)
    return templates.TemplateResponse(request, "history_detail.html", page_ctx(
        request, "evaluate", eval_page="history", run=run,
    ))


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if request.session.get("auth") and store.is_setup_complete():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "setup.html", page_ctx(request, "setup"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("auth"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", page_ctx(request, "login", error=None))


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
