"""Discover and dispatch Tomo modules under ``modules/<id>/``."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Iterable

from modules.base import Module, ModuleMeta, TurnEndContext

logger = logging.getLogger(__name__)

# Built-in modules (import side-effect free constructors).
_BUILTIN: list[str] = [
    "modules.token_monitor",
    "modules.kanban",
]

_instances: list[Module] | None = None


def _load() -> list[Module]:
    global _instances
    if _instances is not None:
        return _instances
    out: list[Module] = []
    for mod_path in _BUILTIN:
        try:
            mod = __import__(mod_path, fromlist=["module"])
            inst = getattr(mod, "module", None)
            if inst is None:
                logger.warning("module %s has no `module` instance", mod_path)
                continue
            out.append(inst)
        except Exception:
            logger.exception("failed to load module %s", mod_path)
    _instances = out
    return out


def iter_modules() -> Iterable[Module]:
    return list(_load())


def all_metas() -> list[ModuleMeta]:
    return [m.meta for m in _load()]


def get_module(module_id: str) -> Module | None:
    mid = (module_id or "").strip()
    for m in _load():
        if m.meta.id == mid:
            return m
    return None


def nav_items(enabled_ids: set[str] | None = None) -> list[dict[str, str]]:
    """Top-nav entries for enabled modules that set ``ModuleMeta.nav_label``.

    Each item: ``{id, label, path, page}`` where ``page`` is the active-nav key
    (``ui_path`` without leading slash).
    """
    if enabled_ids is None:
        # Caller may pass store-backed ids; when omitted, show all with nav_label
        # (used only if DB unavailable — prefer explicit enabled set).
        enabled_ids = {m.meta.id for m in _load()}
    out: list[dict[str, str]] = []
    for m in _load():
        meta = m.meta
        label = (meta.nav_label or "").strip()
        path = (meta.ui_path or "").strip()
        if not label or not path or meta.id not in enabled_ids:
            continue
        page = path.lstrip("/") or meta.id
        out.append(
            {
                "id": meta.id,
                "label": label,
                "path": path,
                "page": page,
            }
        )
    return out


def sync_module_rows(conn: sqlite3.Connection) -> None:
    """Upsert catalog rows from discovered modules (never wipe user enabled flags).

    Inserts missing ids with ``default_enabled``. Does not overwrite existing rows
    so operators can disable modules in the UI permanently.
    """
    import time

    now = time.time()
    for meta in all_metas():
        row = conn.execute("SELECT id FROM modules WHERE id=?", (meta.id,)).fetchone()
        if row:
            continue
        conn.execute(
            "INSERT INTO modules (id, name, description, version, enabled, has_ui, "
            "ui_path, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                meta.id,
                meta.name,
                meta.description,
                meta.version,
                1 if meta.default_enabled else 0,
                1 if meta.has_ui else 0,
                meta.ui_path or "",
                now,
            ),
        )
    conn.commit()


def _enabled_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT id FROM modules WHERE enabled=1").fetchall()
    return {str(r["id"]) for r in rows}


def on_turn_end(conn: sqlite3.Connection, ctx: TurnEndContext) -> None:
    """Dispatch turn-end hooks to enabled modules (passes ``conn`` for ledger writes)."""
    enabled = _enabled_ids(conn)
    for m in _load():
        if m.meta.id not in enabled:
            continue
        hook = getattr(m, "on_turn_end", None)
        if not callable(hook):
            continue
        try:
            hook(ctx, conn)
        except TypeError:
            try:
                hook(ctx)
            except Exception:
                logger.exception("module %s on_turn_end failed", m.meta.id)
        except Exception:
            logger.exception("module %s on_turn_end failed", m.meta.id)


def register_module_routes(api_router: Any) -> None:
    """Let each module attach API routes (modules still gate themselves if disabled)."""
    for m in _load():
        reg = getattr(m, "register_routes", None)
        if not callable(reg):
            continue
        try:
            reg(api_router)
        except Exception:
            logger.exception("module %s register_routes failed", m.meta.id)


def register_module_pages(web_router: Any) -> None:
    """Let each module attach HTML page routes on the web router."""
    for m in _load():
        reg = getattr(m, "register_pages", None)
        if not callable(reg):
            continue
        try:
            reg(web_router)
        except Exception:
            logger.exception("module %s register_pages failed", m.meta.id)


def mount_module_static(app: Any) -> None:
    """Serve ``modules/<id>/static/`` at ``/m/<id>/static/``."""
    from fastapi.staticfiles import StaticFiles

    from modules.paths import module_static_dir

    for m in _load():
        sdir = module_static_dir(m.meta.id)
        if not sdir.is_dir():
            continue
        mount_path = f"/m/{m.meta.id}/static"
        try:
            app.mount(
                mount_path,
                StaticFiles(directory=str(sdir)),
                name=f"module_static_{m.meta.id}",
            )
        except Exception:
            logger.exception("module %s static mount failed", m.meta.id)
