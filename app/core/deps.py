"""Shared FastAPI dependencies: Jinja2 templates, auth/session."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Form, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from .config import EVAL_UI_ENABLED, TEMPLATE_DIR

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
    return bool(request.session.get("auth")) or bool(
        getattr(request.state, "auth_user_id", None)
    )


def _extract_bearer_or_api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    xkey = (request.headers.get("x-api-key") or "").strip()
    return xkey or None


def _try_api_key_auth(request: Request) -> bool:
    """If a valid API key is present, attach identity to ``request.state``."""
    if getattr(request.state, "auth_user_id", None):
        return True
    token = _extract_bearer_or_api_key(request)
    if not token or not token.startswith("tomo_"):
        return False
    from app.services import store

    identity = store.authenticate_api_key(token)
    if not identity:
        return False
    request.state.auth_user_id = identity["user_id"]
    request.state.auth_username = identity["username"]
    request.state.auth_via = "api_key"
    request.state.auth_key_id = identity["key_id"]
    return True


def session_user_id(request: Request) -> str:
    """Logged-in account id (session or API key), or ``web`` when anonymous."""
    api_uid = getattr(request.state, "auth_user_id", None)
    if api_uid:
        return str(api_uid)
    return str(request.session.get("user_id") or "web")


def session_username(request: Request) -> str:
    api_name = getattr(request.state, "auth_username", None)
    if api_name:
        return str(api_name)
    return str(request.session.get("user") or "")


def require_auth(request: Request) -> None:
    """Dependency for routes that need a logged-in admin or API key."""
    if _is_authenticated(request):
        return
    if _try_api_key_auth(request):
        return
    if request.url.path.startswith("/api/"):
        # Invalid Bearer that looks like our key → 401 (don't fall through).
        token = _extract_bearer_or_api_key(request)
        if token and token.startswith("tomo_"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"/login?next={request.url.path}"},
    )


AuthDep = Annotated[None, Depends(require_auth)]


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Verify username+password against SQLite login accounts."""
    from app.services import store

    return store.authenticate(username, password)


def login_form_data(
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next_url: Annotated[str, Form()] = "/",
) -> dict[str, str]:
    return {
        "username": username,
        "password": password,
        "next": next_url or "/",
    }
