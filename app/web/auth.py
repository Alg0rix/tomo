"""Authentication — admin login."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core.config import BRAND
from app.core.deps import templates, verify_password

router = APIRouter()


def _safe_next(target: str) -> str:
    if not target or "://" in target or target.startswith("//"):
        return "/"
    return target if target.startswith("/") else "/"


@router.post("/login")
async def login_submit(
    request: Request,
    password: Annotated[str, Form()] = "",
    next_url: Annotated[str, Form()] = "/",
):
    ctx = {"page": "login", "brand": BRAND, "error": None}
    if verify_password(password):
        request.session["auth"] = True
        request.session["user"] = "admin"
        return RedirectResponse(_safe_next(next_url), status_code=303)
    ctx["error"] = "Incorrect password. Try again."
    return templates.TemplateResponse(request, "login.html", ctx, status_code=401)
