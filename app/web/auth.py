"""Authentication — login account sign-in."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.config import BRAND
from app.core.deps import authenticate, templates

router = APIRouter()


def _safe_next(target: str) -> str:
    """Allow only same-origin relative paths (no scheme / host / protocol-relative)."""
    if not target or not isinstance(target, str):
        return "/"
    parts = urlsplit(target.strip())
    if parts.scheme or parts.netloc:
        return "/"
    path = parts.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    # Drop backslash tricks / nulls.
    if "\\" in path or "\x00" in path:
        return "/"
    query = f"?{parts.query}" if parts.query else ""
    frag = f"#{parts.fragment}" if parts.fragment else ""
    return f"{path}{query}{frag}"


@router.post("/login")
async def login_submit(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
):
    ctx = {"page": "login", "brand": BRAND, "error": None}
    user = authenticate(username.strip(), password)
    if user:
        request.session["auth"] = True
        request.session["user_id"] = user["id"]
        request.session["user"] = user["username"]
        return RedirectResponse(_safe_next(next), status_code=303)
    ctx["error"] = "Incorrect username or password."
    return templates.TemplateResponse(request, "login.html", ctx, status_code=401)
