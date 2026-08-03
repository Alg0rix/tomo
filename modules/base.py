"""Shared types for Tomo modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ModuleMeta:
    """Catalog metadata shown in Modules UI and stored in SQLite ``modules``."""

    id: str
    name: str
    description: str
    version: str = "0.1"
    has_ui: bool = False
    ui_path: str = ""
    # When set (and module enabled), shown as a top-nav link to ``ui_path``.
    nav_label: str = ""
    default_enabled: bool = True


@dataclass
class TurnEndContext:
    """Fired after a session turn finishes (Token Monitor, analytics, …)."""

    session_id: str
    agent_id: str
    message: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Module(Protocol):
    """Optional hooks a module package may implement."""

    meta: ModuleMeta

    def on_turn_end(self, ctx: TurnEndContext) -> None:
        """Called when a chat turn completes (only if module is enabled).

        Registry may pass ``conn`` as a second positional arg for DB writes.
        """

    def register_routes(self, api_router: Any) -> None:
        """Attach FastAPI routes (usually delegates to package ``routes.py``)."""

    def register_pages(self, web_router: Any) -> None:
        """Attach HTML page routes (usually delegates to package ``routes.py``).

        Templates live in ``modules/<id>/templates/`` (Jinja name
        ``<id>/page.html``). Static assets under ``modules/<id>/static/``
        are served at ``/m/<id>/static/…``.
        """
