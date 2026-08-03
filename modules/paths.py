"""Filesystem paths for Tomo modules packages."""

from __future__ import annotations

from pathlib import Path

MODULES_ROOT = Path(__file__).resolve().parent


def module_package_dir(module_id: str) -> Path:
    return MODULES_ROOT / module_id


def module_templates_dir(module_id: str) -> Path:
    return module_package_dir(module_id) / "templates"


def module_static_dir(module_id: str) -> Path:
    return module_package_dir(module_id) / "static"


def static_url(module_id: str, filename: str) -> str:
    """Public URL for a file under ``modules/<id>/static/``."""
    name = (filename or "").lstrip("/")
    return f"/m/{module_id}/static/{name}"
