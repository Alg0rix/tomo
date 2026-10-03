"""Authenticated plugin management API and gallery."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.deps import AuthDep, session_user_id, templates
from app.plugins.manager import get_manager
from app.plugins.catalogs import get_marketplaces
from app.web.context import page_ctx

router = APIRouter()


def require_plugin_admin(request: Request, _: AuthDep):
    from app.services import store

    user = store.get_user(session_user_id(request))
    if not user or not user.get("enabled") or user.get("role") != "admin":
        raise HTTPException(403, "Plugin management requires an administrator")
    if request.method != "GET":
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and urlsplit(origin).netloc != request.headers.get("host")
        ):
            raise HTTPException(403, "Cross-origin plugin management is forbidden")


class InstallBody(BaseModel):
    path: str
    subdirectory: str = ""
    ref: str = "main"


class CheckUpdatesBody(BaseModel):
    force: bool = True


async def operate(function, *args):
    try:
        return await run_in_threadpool(function, *args)
    except KeyError:
        raise HTTPException(404, "Plugin not found") from None
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Plugin activation failed")
        raise HTTPException(
            400, "Plugin activation failed; check the server log"
        ) from None


@router.get("/api/plugins")
def list_plugins(_: AuthDep):
    return get_manager().list()


@router.post("/api/plugins/install", dependencies=[Depends(require_plugin_admin)])
async def install_plugin(body: InstallBody):
    return await operate(get_manager().install, body.path, body.subdirectory, body.ref)


@router.post("/api/plugins/check-updates", dependencies=[Depends(require_plugin_admin)])
async def check_plugin_updates(body: CheckUpdatesBody):
    return await operate(get_manager().check_updates, body.force)


@router.post(
    "/api/plugins/{plugin_id}/{action}", dependencies=[Depends(require_plugin_admin)]
)
async def change_plugin(plugin_id: str, action: str):
    return await operate(get_manager().change, plugin_id, action)


@router.get("/extensions")
def plugins_page(request: Request, _: AuthDep):
    from app.services import store

    user = store.get_user(session_user_id(request)) or {}
    return templates.TemplateResponse(
        request,
        "plugins.html",
        page_ctx(
            request,
            "plugins",
            plugins=get_manager().list(),
            catalog=get_marketplaces().search(),
            marketplaces=get_marketplaces().list(),
            builder_agents=[a for a in store.list_agents() if a.get("enabled")],
            builder_workplaces=[
                w for w in store.list_workplaces() if w.get("kind") == "local"
            ],
            can_manage_plugins=user.get("role") == "admin",
        ),
    )


class MarketplaceBody(BaseModel):
    source: str


@router.get("/api/marketplaces")
def list_marketplaces(_: AuthDep):

    return get_marketplaces().list()


@router.get("/api/plugins/catalog")
def search_catalog(_: AuthDep, q: str = ""):

    return get_marketplaces().search(q)


@router.post("/api/marketplaces", dependencies=[Depends(require_plugin_admin)])
async def add_marketplace(body: MarketplaceBody):

    return await operate(get_marketplaces().add, body.source)


@router.post(
    "/api/marketplaces/{identity}/refresh", dependencies=[Depends(require_plugin_admin)]
)
async def refresh_marketplace(identity: str):

    return await operate(get_marketplaces().refresh, identity)


@router.delete(
    "/api/marketplaces/{identity}", dependencies=[Depends(require_plugin_admin)]
)
async def remove_marketplace(identity: str):

    return await operate(get_marketplaces().remove, identity)


@router.get("/settings/marketplaces")
def marketplaces_page(request: Request, _: AuthDep):
    from app.services import store

    return templates.TemplateResponse(
        request,
        "marketplaces.html",
        page_ctx(
            request,
            "marketplaces",
            marketplaces=get_marketplaces().list(),
            can_manage_plugins=(store.get_user(session_user_id(request)) or {}).get(
                "role"
            )
            == "admin",
        ),
    )


@router.get("/extensions/guide")
def plugin_guide(request: Request, _: AuthDep):
    return templates.TemplateResponse(
        request, "plugin_guide.html", page_ctx(request, "plugins")
    )


@router.get("/api/plugins/ideas", dependencies=[Depends(require_plugin_admin)])
async def plugin_ideas(request: Request, refresh: bool = False):
    from app.runtime.plugin_ideas import get_plugin_ideas

    return await get_plugin_ideas(session_user_id(request), refresh=refresh)
