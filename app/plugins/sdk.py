"""Small public SDK. Plugins export a synchronous ``setup(api)`` function."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader


def _is_kanji(value: object) -> bool:
    """One CJK ideograph or kana (Home room icons use the kanji font)."""
    if not isinstance(value, str) or len(value) != 1:
        return False
    code = ord(value)
    return 0x3040 <= code <= 0x30FF or 0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF


class PluginAPI:
    def __init__(self, plugin_id: str, path: Path, data_dir: Path):
        self.id = plugin_id
        self.path = path
        self.data_dir = data_dir
        self.router = APIRouter()
        self.public_router = APIRouter()
        self.pages: list[dict] = []
        self.tools: dict[str, tuple[dict, Callable]] = {}
        self.skills: list = []
        self.home_cards: list[dict] = []
        self.starters: list[dict] = []
        self._turn_end: list[Callable] = []
        self._cleanup: list[Callable] = []
        from app.plugins.services import PluginSettings

        self.settings = PluginSettings(data_dir)
        self._background: list = []
        self._activated = False
        self._disposed = False

    @property
    def base_url(self) -> str:
        return f"/plugins/{self.id}"

    @property
    def public_base_url(self) -> str:
        return self.base_url + "/public"

    @property
    def static_url(self) -> str:
        return self.base_url + "/static"

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

    def home_card(
        self,
        handler: Callable,
        *,
        title: str | None = None,
        size: str = "s",
        kanji: str | None = None,
        id: str | None = None,
        default_visible: bool | None = None,
        refresh_seconds: int | None = None,
    ) -> None:
        """Contribute a card to the Home page's Rooms grid.

        ``handler(user_id)`` runs on Home load and configured refreshes, returning a typed card
        dict (metric, stats, chart, ring, heatmap, list, timeline, columns,
        actions, …). Core renders it; plugins never inject HTML into Home.
        ``size`` is ``s`` (one column), ``m`` (two), or ``l`` (full row);
        ``kanji`` is an optional single CJK character used as the room icon.
        ``id`` is a stable name within the plugin. Up to twelve cards can be
        registered. Only the first named card is visible by default; unnamed
        cards retain their previous defaults. Users choose their own widgets
        and sizes. ``default_visible`` overrides the initial visibility.
        ``refresh_seconds`` opts into periodic refresh while Home is visible
        (integer seconds, 5–3600); omitted cards refresh only on Home load.
        """
        import inspect

        if not callable(handler) or inspect.iscoroutinefunction(handler):
            raise ValueError("Home cards require a synchronous handler")
        if size not in {"s", "m", "l"}:
            raise ValueError("Home card size must be 's', 'm', or 'l'")
        if kanji is not None and not _is_kanji(kanji):
            raise ValueError("Home card kanji must be a single CJK character")
        import re

        if id is not None and (not isinstance(id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", id)):
            raise ValueError("Home card id must be a lowercase name, up to 64 characters")
        if id is not None and any(card.get("id") == id for card in self.home_cards):
            raise ValueError("Home card ids must be unique within a plugin")
        if default_visible is not None and not isinstance(default_visible, bool):
            raise ValueError("default_visible must be a boolean")
        if refresh_seconds is not None and (
            type(refresh_seconds) is not int or not 5 <= refresh_seconds <= 3600
        ):
            raise ValueError("refresh_seconds must be an integer between 5 and 3600")
        if len(self.home_cards) >= 12:
            raise ValueError("A plugin may register at most twelve home cards")
        self.home_cards.append(
            {
                "handler": handler,
                "title": (title or "").strip()[:40],
                "size": size,
                "kanji": kanji or "",
                "id": id,
                "refresh_seconds": refresh_seconds,
                "default_visible": default_visible if default_visible is not None else (id is None or not self.home_cards),
            }
        )

    def starter(self, label: str, prompt: str) -> None:
        """Suggest a prompt in the Home composer's starter chips."""
        label = (label or "").strip()
        prompt = (prompt or "").strip()
        if not label or not prompt or len(label) > 60 or len(prompt) > 500:
            raise ValueError("Starters need a label (≤60) and prompt (≤500)")
        if len(self.starters) >= 4:
            raise ValueError("A plugin may register at most four starters")
        self.starters.append({"label": label, "prompt": prompt})

    def user_data_dir(self, user_id: str | None = None) -> Path:
        if user_id is None:
            from app.runtime.tools.user_ctx import current_user_id

            user_id = current_user_id()
        path = self.data_dir / "users" / hashlib.sha256(user_id.encode()).hexdigest()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _workplace_user(self, user_id: str | None) -> None:
        self._require_live()
        from app.runtime.tools.user_ctx import current_user_id
        from app.services import store

        user = store.get_user(user_id if user_id is not None else current_user_id())
        # Workplaces are global today; do not imply a per-user ACL exists.
        if not user or not user.get("enabled") or user.get("role") != "admin":
            raise PermissionError("Workplace SDK access requires an active administrator")

    def list_workplaces(self, *, user_id: str | None = None) -> list[dict]:
        """List enabled tunnel workplaces, without credentials or pairing codes.

        HTTP handlers must pass the authenticated account ID. Tools and Home
        cards can use the bound account. Only active administrators have access.
        """
        from app.services import store
        from app.workplaces.hub import hub

        self._workplace_user(user_id)
        return [
            {
                "id": wp["id"],
                "name": wp["name"],
                "kind": "tunnel",
                "online": hub.is_online(wp["id"]),
                "hostname": wp.get("connector_hostname") or "",
                "version": wp.get("connector_version") or "",
                "last_seen_at": wp.get("connector_last_seen_at") or 0,
            }
            for wp in store.list_workplaces()
            if wp.get("enabled") and wp.get("kind") == "tunnel"
        ]

    def workplace_status(self, workplace_id: str, *, user_id: str | None = None) -> dict:
        """Return live connector connectivity, not a server health assessment."""
        for wp in self.list_workplaces(user_id=user_id):
            if wp["id"] == workplace_id:
                return wp
        raise ValueError("Enabled tunnel workplace not found")

    def exec_workplace(
        self,
        workplace_id: str,
        command: str,
        *,
        timeout: float = 10,
        user_id: str | None = None,
    ) -> dict:
        """Run bash on an explicit tunnel; return stdout/stderr/exit_code.

        Synchronous: use a sync HTTP handler or offload to a worker thread.
        Offline/transport failures raise ConnectionError, never run locally.
        Nonzero command exits are returned normally. This does not use agent
        tool approvals or inject secret capabilities; plugins must enforce
        their own command policy.
        """
        import math

        from app.workplaces.hub import hub

        wp = self.workplace_status(workplace_id, user_id=user_id)
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Command must be a nonempty string")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 60
        ):
            raise ValueError("Timeout must be finite and between 0 and 60 seconds")
        if not wp["online"]:
            raise ConnectionError("Tunnel workplace is offline")
        payload = hub.call(
            workplace_id, "exec_bash", {"command": command, "timeout": timeout},
            timeout=timeout + 5,
        )
        if not payload.get("ok"):
            raise ConnectionError(str(payload.get("error") or "Tunnel execution failed"))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ConnectionError("Invalid tunnel execution response")
        return result

    def read_workplace_file(
        self, workplace_id: str, path: str, *, max_bytes: int = 65536,
        timeout: float = 10, user_id: str | None = None,
    ) -> dict:
        """Read bounded UTF-8 text via tunnel bash (requires POSIX head).

        Relative paths use the connector work root; binary files are not
        supported. This shares exec_workplace's admin and availability checks.
        """
        import shlex

        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("Path must be a nonempty string without NUL")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
            raise ValueError("max_bytes must be between 1 and 1048576")
        result = self.exec_workplace(
            workplace_id, f"head -c {max_bytes + 1} -- {shlex.quote(path)}",
            timeout=timeout, user_id=user_id,
        )
        if result.get("exit_code") != 0:
            raise OSError(result.get("stderr") or "Remote file read failed")
        data = result.get("stdout", "").encode("utf-8")
        return {"path": path, "content": data[:max_bytes].decode("utf-8", errors="replace"), "truncated": len(data) > max_bytes}

    def background_task(self, callback: Callable, *, interval_seconds: float = 10) -> None:
        """Register a periodic sync callback(stop_event), started after activation.

        Calls never overlap within a task. Use stop_event for cooperative
        cancellation and bounded I/O; no authenticated user is inherited.
        """
        import inspect
        import math
        from app.plugins.services import BackgroundTask

        if self._activated or self._disposed:
            raise RuntimeError("Background tasks must be registered during setup")
        if not callable(callback) or inspect.iscoroutinefunction(callback):
            raise ValueError("Background callback must be synchronous")
        inspect.signature(callback).bind(None)
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds)
            or not 1 <= interval_seconds <= 3600
        ):
            raise ValueError("Background interval must be between 1 and 3600 seconds")
        if len(self._background) >= 8:
            raise ValueError("A plugin may register at most eight background tasks")
        self._background.append(BackgroundTask(self.id, callback, interval_seconds))

    def _activate(self) -> None:
        import logging

        if self._activated or self._disposed:
            return
        self._activated = True
        for task in self._background:
            try:
                task.start()
            except Exception:
                logging.getLogger(__name__).exception("Plugin %s worker start failed", self.id)

    def _require_live(self) -> None:
        if self._disposed:
            raise RuntimeError("Plugin has been disposed")

    def capture_notification_target(self, *, user_id: str | None = None) -> str:
        """Capture the current owned channel destination as an opaque saved ID."""
        from app.plugins.services import capture_notification

        self._require_live()
        return capture_notification(self, user_id)

    async def notify(self, target_id: str, message: str, *, user_id: str | None = None) -> dict:
        """Send to a captured destination, rechecking account/channel access."""
        from app.plugins.services import notify

        self._require_live()
        return await notify(self, target_id, message, user_id)

    async def generate(
        self, prompt: str, *, profile_id: str | None = None,
        max_output_tokens: int = 1024, timeout: float = 60,
        user_id: str | None = None,
    ) -> dict:
        """One tool-free LLM call using Tomo profiles, with recorded usage."""
        from app.plugins.services import generate

        self._require_live()
        return await generate(
            self, prompt, profile_id=profile_id, max_output_tokens=max_output_tokens,
            timeout=timeout, user_id=user_id,
        )

    async def agent(
        self, prompt: str, *, user_id: str | None = None, agent_id: str | None = None,
    ) -> dict:
        """Start one tool-using agent turn in a new session owned by the account.

        Returns ``session_id``, ``agent_id``, and ``status`` once the turn is
        accepted. The account's current grants apply. From a worker thread use
        ``asyncio.run``; the turn stays on the server loop.
        """
        from app.plugins.services import agent

        self._require_live()
        return await agent(self, prompt, user_id=user_id, agent_id=agent_id)

    def schedule(
        self, name: str, prompt: str, *, when: str,
        user_id: str | None = None, agent_id: str | None = None,
    ) -> dict:
        """Create a routine the account owns and can pause in Routines.

        ``when`` is a schedule string such as ``every 30m`` or ``0 9 * * *``.
        Fires run in a fresh session.
        """
        from app.plugins.services import schedule

        self._require_live()
        return schedule(
            self, name, prompt, when=when, user_id=user_id, agent_id=agent_id,
        )

    def unschedule(self, schedule_id: str, *, user_id: str | None = None) -> None:
        """Delete a routine this plugin created for the account."""
        from app.plugins.services import unschedule

        self._require_live()
        unschedule(self, schedule_id, user_id=user_id)

    def schedules(self, *, user_id: str | None = None) -> list[dict]:
        """Routines this plugin created for the account."""
        from app.plugins.services import schedules

        self._require_live()
        return schedules(self, user_id=user_id)

    def render(self, request: Request, template: str, **context):
        from app.web.context import page_ctx

        return self._render(
            request,
            template,
            page_ctx(
                request,
                f"plugin-{self.id}",
                plugin={
                    "id": self.id,
                    "base_url": self.base_url,
                    "public_base_url": self.public_base_url,
                    "static_url": self.static_url,
                    "pages": self.pages,
                },
                **context,
            ),
        )

    def render_public(self, request: Request, template: str, **context):
        """Render a standalone public template without account/navigation context."""
        return self._render(
            request,
            template,
            {
                "plugin": {
                    "id": self.id,
                    "base_url": self.public_base_url,
                    "public_base_url": self.public_base_url,
                    "static_url": self.static_url,
                },
                **context,
            },
        )

    def _render(self, request: Request, template: str, context: dict):
        from app.core.deps import templates

        renderer = Jinja2Templates(directory=str(self.path / "templates"))
        renderer.env.loader = ChoiceLoader(
            [FileSystemLoader(str(self.path / "templates")), templates.env.loader]
        )
        renderer.env.globals.update(templates.env.globals)
        return renderer.TemplateResponse(
            request,
            template,
            context,
        )

    def on_turn_end(self, callback: Callable) -> None:
        """Observe completed Tomo turns while this plugin is enabled."""
        self._turn_end.append(callback)

    def on_dispose(self, callback: Callable) -> None:
        self._cleanup.append(callback)

    def dispose(self) -> None:
        import logging
        import time

        if self._disposed:
            return
        self._disposed = True
        for task in self._background:
            task.cancel()
        deadline = time.monotonic() + 5
        for task in self._background:
            task.join(timeout=max(0, deadline - time.monotonic()))
        for callback in reversed(self._cleanup):
            try:
                callback()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Plugin %s cleanup failed", self.id
                )
        self._cleanup.clear()
