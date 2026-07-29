"""Authentication — login account sign-in."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.config import BRAND
from app.core.deps import authenticate, templates

router = APIRouter()


def _safe_next(target: str) -> str:
    if not target or "://" in target or target.startswith("//"):
        return "/"
    return target if target.startswith("/") else "/"


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
