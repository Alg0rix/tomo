"""Authentication — login account sign-in."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.config import BRAND
from app.core.deps import authenticate, templates
from app.workplaces.pairing import PairingRateLimiter

router = APIRouter()

# Brute-force guard for password login (per client IP).
_login_limiter = PairingRateLimiter(max_attempts=20, window_seconds=300)


def safe_next_path(target: str) -> str:
    """Allow only same-origin relative paths (no scheme / host / protocol-relative).

    Uses the CodeQL-recommended ``urlparse`` empty-scheme/netloc guard after
    normalizing backslashes (browsers treat ``\\`` like ``/``).
    """
    if not target or not isinstance(target, str):
        return "/"
    # Browsers accept ``\\`` as ``/``; urlparse does not.
    cleaned = target.strip().replace("\\", "")
    if "\x00" in cleaned:
        return "/"
    parsed = urlparse(cleaned)
    if parsed.scheme or parsed.netloc:
        return "/"
    path = parsed.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    query = f"?{parsed.query}" if parsed.query else ""
    frag = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{path}{query}{frag}"


def _login_client_ip(request: Request) -> str:
    # Do not trust X-Forwarded-For here (spoofable); peer address only.
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


@router.post("/login")
async def login_submit(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
):
    ctx = {"page": "login", "brand": BRAND, "error": None}
    ip = _login_client_ip(request)
    if not _login_limiter.allow(f"login:{ip}"):
        ctx["error"] = "Too many login attempts. Try again later."
        return templates.TemplateResponse(request, "login.html", ctx, status_code=429)
    user = authenticate(username.strip(), password)
    if user:
        request.session["auth"] = True
        request.session["user_id"] = user["id"]
        request.session["user"] = user["username"]
        # CodeQL py/url-redirection: sanitize with urlparse at the redirect site.
        # See https://codeql.github.com/codeql-query-help/python/py-url-redirection/
        target = (next or "/").replace("\\", "")
        parsed = urlparse(target)
        if (
            not parsed.netloc
            and not parsed.scheme
            and (parsed.path or "/").startswith("/")
            and not (parsed.path or "").startswith("//")
        ):
            return RedirectResponse(target, status_code=303)
        return RedirectResponse("/", status_code=303)
    ctx["error"] = "Incorrect username or password."
    return templates.TemplateResponse(request, "login.html", ctx, status_code=401)
