"""HTTP API for script-install self-update."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.deps import AuthDep
from app.core.self_update import check, start_update, status

router = APIRouter(prefix="/api")

_REASON_DETAIL = {
    "container": "This Tomo is running in a container. Pull a new image instead.",
    "not_script_install": "Self-update is only available for script installs.",
}


def _http_detail(exc: BaseException) -> str:
    key = str(exc)
    return _REASON_DETAIL.get(key, key)


@router.get("/update")
def get_update(_: AuthDep):
    return status()


@router.post("/update/check")
def check_update(_: AuthDep):
    try:
        return check()
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=_http_detail(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/update")
def post_update(_: AuthDep):
    try:
        start_update()
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=_http_detail(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"started": True, "updating": True}
