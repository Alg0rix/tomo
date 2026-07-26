"""Shared FastAPI dependencies: Jinja2 templates, auth/session."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Form, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from .config import ADMIN_PASSWORD, EVAL_UI_ENABLED, TEMPLATE_DIR

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

_AVATAR_HUES = [200, 260, 330, 160, 30, 290, 80, 10]


def _hash(s: str) -> int:
    h = 0
    for ch in s:
        h = ((h << 5) - h) + ord(ch)
        h &= 0xFFFFFFFF
    return h


def avatar_color(agent_id: str) -> str:
    """Deterministic HSL background for an agent avatar, dark-friendly."""
    hue = _AVATAR_HUES[_hash(agent_id) % len(_AVATAR_HUES)]
    return f"hsl({hue}, 62%, 42%)"


def ts(value: float | int | str) -> str:
    """Format a timestamp as a short relative/absolute string for templates."""
    import time as _t

    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    now = _t.time()
    delta = now - v
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 86400 * 7:
        return f"{int(delta // 86400)}d ago"
    return _t.strftime("%b %d", _t.localtime(v))


templates.env.globals["avatar_color"] = avatar_color
templates.env.globals["ts"] = ts
templates.env.globals["eval_ui_enabled"] = EVAL_UI_ENABLED


def _is_authenticated(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_auth(request: Request) -> None:
    """Dependency for routes that need a logged-in admin."""
    if _is_authenticated(request):
        return
    if request.url.path.startswith("/api/"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/login?next={request.url.path}"},
    )


AuthDep = Annotated[None, Depends(require_auth)]


def verify_password(password: str) -> bool:
    return password == ADMIN_PASSWORD


def login_form_data(
    password: Annotated[str, Form()],
    next_url: Annotated[str, Form()] = "/",
) -> dict[str, str]:
    return {"password": password, "next": next_url or "/"}
