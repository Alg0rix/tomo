"""Small public SDK. Plugins export a synchronous ``setup(api)`` function."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader


class PluginAPI:
    def __init__(self, plugin_id: str, path: Path, data_dir: Path):
        self.id = plugin_id
        self.path = path
        self.data_dir = data_dir
        self.router = APIRouter()
        self.pages: list[dict] = []
        self.tools: dict[str, tuple[dict, Callable]] = {}
        self.skills: list = []
        self._turn_end: list[Callable] = []
        self._cleanup: list[Callable] = []

    @property
    def base_url(self) -> str:
        return f"/plugins/{self.id}"

    def page(self, path: str, label: str) -> None:
        if (
            not path.startswith("/")
            or path.startswith("//")
            or ".." in path
            or "?" in path
            or "#" in path
        ):
            raise ValueError("Page paths must be local absolute paths")
        self.pages.append({"label": label, "path": self.base_url + path})

    def tool(
        self, name: str, description: str, parameters: dict, handler: Callable
    ) -> str:
        import re
        import inspect

        if (
            not re.fullmatch(r"[a-z][a-z0-9_]*", name)
            or not callable(handler)
            or inspect.iscoroutinefunction(handler)
        ):
            raise ValueError("Tools require a simple name and synchronous handler")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError("Tool parameters must be an object JSON schema")
        tool_id = f"plugin__{self.id}__{name}"
        if len(tool_id) > 64:
            raise ValueError("Namespaced tool names must fit 64 characters")
        if tool_id in self.tools:
            raise ValueError(f"Duplicate tool: {tool_id}")
        self.tools[tool_id] = (
            {
                "id": tool_id,
                "name": name,
                "description": description,
                "backend": f"plugin:{self.id}",
                "schema": {
                    "type": "function",
                    "function": {
                        "name": tool_id,
                        "description": description,
                        "parameters": parameters,
                    },
                },
            },
            handler,
        )
        return tool_id

    def user_data_dir(self, user_id: str | None = None) -> Path:
        if user_id is None:
            from app.runtime.tools.user_ctx import current_user_id

            user_id = current_user_id()
        path = self.data_dir / "users" / hashlib.sha256(user_id.encode()).hexdigest()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def render(self, request: Request, template: str, **context):
        from app.core.deps import templates
        from app.web.context import page_ctx

        renderer = Jinja2Templates(directory=str(self.path / "templates"))
        renderer.env.loader = ChoiceLoader(
            [FileSystemLoader(str(self.path / "templates")), templates.env.loader]
        )
        renderer.env.globals.update(templates.env.globals)
        return renderer.TemplateResponse(
            request,
            template,
            page_ctx(
                request,
                f"plugin-{self.id}",
                plugin={"id": self.id, "base_url": self.base_url, "pages": self.pages},
                **context,
            ),
        )

    def on_turn_end(self, callback: Callable) -> None:
        """Observe completed Tomo turns while this plugin is enabled."""
        self._turn_end.append(callback)

    def on_dispose(self, callback: Callable) -> None:
        self._cleanup.append(callback)

    def dispose(self) -> None:
        import logging

        for callback in reversed(self._cleanup):
            try:
                callback()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Plugin %s cleanup failed", self.id
                )
        self._cleanup.clear()
