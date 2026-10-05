"""Shared FastAPI dependencies: Jinja2 templates, auth/session."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Form, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from .config import EVAL_UI_ENABLED, TEMPLATE_DIR


from app.plugins.icons import ICONS

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["plugin_icons"] = ICONS

# Darkroom signal hues: sky / rose / jade / amber / warm-earth (no indigo/violet)
_AVATAR_HUES = [200, 350, 160, 30, 12, 85, 195, 45]


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

# Static-asset cache buster. Hand-bumped ``?v=`` tags drift — a JS change that
# forgets the bump ships stale code to browsers. The tag fingerprints the files
# themselves, so a CDN edge (Cloudflare caches /static for hours) never serves
# old assets after a deploy, even when the server process was not restarted.
import hashlib as _hashlib  # noqa: E402
import time as _time  # noqa: E402

from .config import STATIC_DIR  # noqa: E402
from .self_update import package_version as _pkg_version  # noqa: E402


class _StaticVersion:
    _TTL = 2.0

    def __init__(self) -> None:
        self._value = ""
        self._checked = 0.0

    def __str__(self) -> str:
        now = _time.monotonic()
        if not self._value or now - self._checked > self._TTL:
            digest = _hashlib.sha1()
            for path in sorted(STATIC_DIR.rglob("*")):
                if path.is_file():
                    st = path.stat()
                    digest.update(
                        f"{path.relative_to(STATIC_DIR)}:{st.st_mtime_ns}:{st.st_size}\n".encode()
                    )
            self._value = f"{_pkg_version()}-{digest.hexdigest()[:10]}"
            self._checked = now
        return self._value


templates.env.globals["static_ver"] = _StaticVersion()


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


def authenticated_user(request: Request) -> dict[str, Any]:
    """Current enabled account, never a role copied from a signed cookie."""
    require_auth(request)
    from app.services import store

    uid = getattr(request.state, "auth_user_id", None) or request.session.get("user_id")
    user = store.get_user(str(uid or ""))
    if not user or not user["enabled"]:
        raise HTTPException(status_code=401, detail="Account is unavailable")
    return user


def session_user_id(request: Request) -> str:
    """Authenticated account id only; anonymous/channel fallbacks are forbidden."""
    return authenticated_user(request)["id"]


def session_username(request: Request) -> str:
    return authenticated_user(request)["username"]


def can_manage_telegram(request: Request) -> bool:
    from app.services import store

    user = store.get_user(session_user_id(request))
    return bool(user and user.get("enabled") and user.get("role") == "admin")


def visible_sessions(request: Request) -> list[dict]:
    """The account's sessions across every channel."""
    from app.services import store

    uid = session_user_id(request)
    return store.list_sessions(user_id=uid)


def require_owned_session(request: Request, session_id: str) -> dict:
    """Load an account-owned session, regardless of channel.

    Missing or other-users' sessions both return 404 so existence is not leaked.
    """
    from app.services import store

    uid = session_user_id(request)
    session = store.get_owned_session(session_id, uid)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )
    return session


def require_auth(request: Request) -> None:
    """Authenticate then re-read enabled state on EVERY request."""
    from app.services import store

    if not getattr(request.state, "auth_user_id", None):
        key_authenticated = _try_api_key_auth(request)
        if _extract_bearer_or_api_key(request) and not key_authenticated:
            # Explicit failed credentials cannot inherit a privileged cookie.
            raise HTTPException(status_code=401, detail="Invalid API key")
    uid = getattr(request.state, "auth_user_id", None)
    if not uid and request.session.get("auth"):
        uid = request.session.get("user_id")
    user = store.get_user(str(uid or "")) if uid else None
    if user and user["enabled"] and user["role"] in ("admin", "member"):
        return
    if request.url.path.startswith("/api/") or request.url.path.startswith("/v1/"):
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


def require_admin(request: Request) -> None:
    if authenticated_user(request)["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin permission is required")


AdminDep = Annotated[None, Depends(require_admin)]


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
