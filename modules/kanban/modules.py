"""Kanban module definition (metadata + hooks).

HTTP wiring lives in :mod:`modules.kanban.routes`.
"""

from __future__ import annotations

from typing import Any

from modules.base import ModuleMeta

META = ModuleMeta(
    id="kanban",
    name="Task Board",
    description="Kanban board for agent-driven task workflows",
    version="0.3",
    has_ui=True,
    ui_path="/board",
    nav_label="Board",
    default_enabled=True,
)


class KanbanModule:
    meta = META

    def register_routes(self, api_router: Any) -> None:
        from .routes import register_api

        register_api(api_router)

    def register_pages(self, web_router: Any) -> None:
        from .routes import register_pages

        register_pages(web_router)


module = KanbanModule()
