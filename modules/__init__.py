"""Tomo modules — optional product surfaces (Token Monitor, Kanban, …).

Discoverable packages under this directory. See ``README.md`` for how to add one.
"""

from __future__ import annotations

from modules.base import ModuleMeta, TurnEndContext
from modules.registry import (
    all_metas,
    get_module,
    iter_modules,
    mount_module_static,
    nav_items,
    on_turn_end,
    register_module_pages,
    register_module_routes,
    sync_module_rows,
)

__all__ = [
    "ModuleMeta",
    "TurnEndContext",
    "all_metas",
    "get_module",
    "iter_modules",
    "mount_module_static",
    "nav_items",
    "on_turn_end",
    "register_module_pages",
    "register_module_routes",
    "sync_module_rows",
]
