"""Live plugin registry. Mutations are serialized; busy plugins cannot reload."""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
from pathlib import Path
import re
import sys
import threading
import types
import uuid

from fastapi import Depends, FastAPI
from starlette.responses import JSONResponse
from starlette.routing import Mount, Match

from app.plugins.sdk import PluginAPI

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}
        self._runtime_started = False
        self._busy: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._state_path = root / "plugins" / "registry.json"
        self._rows: dict[str, dict] = {}
        if self._state_path.exists():
            self._rows = json.loads(self._state_path.read_text())

    def start(self) -> None:
        """Activate persisted plugins in the server, never in a CLI consumer."""
        with self._lock:
            self._runtime_started = True
            for plugin_id, row in list(self._rows.items()):
                if row.get("source") in {"builtin", "official-pending"}:
                    try:
                        row = self._move_official_source(plugin_id)
                    except Exception as exc:
                        self._errors[plugin_id] = (
                            "Official plugin migration failed: " + str(exc)
                        )
                        logger.exception(
                            "Could not migrate official plugin %s", plugin_id
                        )
                        continue
                if row.get("enabled") and plugin_id not in self._active:
                    try:
                        instance = self._build(Path(row["path"]))
                        if instance["meta"]["id"] != plugin_id:
                            instance["api"].dispose()
                            raise ValueError("Installed plugin id changed")
                        self._active[plugin_id] = instance
                        self._errors.pop(plugin_id, None)
                    except Exception as exc:
                        self._errors[plugin_id] = str(exc)
                        logger.exception("Plugin %s activation failed", plugin_id)

    def _move_official_source(self, plugin_id: str) -> dict:
        """One-time extraction of legacy bundled plugins into installed sources."""
        from app.plugins.catalogs import prepare_install
        from app.plugins.migration import OFFICIAL_PLUGINS

        if plugin_id not in OFFICIAL_PLUGINS:
            raise ValueError("Unknown legacy official plugin")
        path, origin, _, download = prepare_install(
            "https://github.com/Alg0rix/tomo-plugins.git",
            self.root,
            "plugins/" + plugin_id,
        )
        old = self._rows[plugin_id]
        try:
            meta = self.manifest(path)
            if meta["id"] != plugin_id:
                raise ValueError(
                    "Official plugin identity differs from installed identity"
                )
            row = {
                **old,
                **meta,
                **origin,
                "path": str(path),
                "marketplace": "tomo-official",
                "author": "Alg0rix",
            }
            self._rows[plugin_id] = row
            self._save()
            self._errors.pop(plugin_id, None)
            return row
        except Exception:
            self._rows[plugin_id] = old
            if download:
                import shutil

                shutil.rmtree(download)
            raise

    def _save(self):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._rows, indent=2))
        tmp.replace(self._state_path)

    @staticmethod
    def manifest(path: Path) -> dict:
        row = json.loads((path / "tomo-plugin.json").read_text())
        if not isinstance(row, dict) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,31}", str(row.get("id", ""))
        ):
            raise ValueError(
                "Plugin id must use lowercase letters, numbers, and underscores"
            )
        if (
            row.get("sdk_version") != 1
            or not isinstance(row.get("name"), str)
            or not row["name"].strip()
        ):
            raise ValueError("Plugin requires a name and sdk_version: 1")
        from app.plugins.icons import validate_icon

        return {"icon": validate_icon(row.get("icon", "puzzle"))} | {
            key: row.get(key, "")
            for key in ("id", "name", "description", "version", "sdk_version")
        }

    def _build(self, path: Path) -> dict:
        from app.core.deps import require_auth

        meta = self.manifest(path)
        api = PluginAPI(meta["id"], path, self.root / "plugins" / "data" / meta["id"])
        # A fresh package namespace and direct compilation avoid stale pyc and
        # support relative imports of helper modules across reloads.
        namespace = "_tomo_plugin_" + uuid.uuid4().hex
        module = types.ModuleType(namespace)
        module.__path__ = [str(path)]
        module.__package__ = namespace
        module.__file__ = str(path / "plugin.py")
        sys.modules[namespace] = module
        api.on_dispose(
            lambda: [
                sys.modules.pop(key, None)
                for key in list(sys.modules)
                if key == namespace or key.startswith(namespace + ".")
            ]
        )
        try:
            # Remove bytecode for helper imports so rapid edits are visible.
            import shutil

            for cache in path.rglob("__pycache__"):
                shutil.rmtree(cache)
            exec(
                compile((path / "plugin.py").read_bytes(), module.__file__, "exec"),
                module.__dict__,
            )
            import inspect

            if not callable(
                getattr(module, "setup", None)
            ) or inspect.iscoroutinefunction(module.setup):
                raise ValueError("Plugin must export synchronous setup(api)")
            result = module.setup(api)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise ValueError("Plugin setup must be synchronous")
            app = FastAPI(
                docs_url=None,
                redoc_url=None,
                openapi_url=None,
                dependencies=[Depends(require_auth)],
            )
            app.include_router(api.router)
            if (path / "static").is_dir():
                from fastapi.staticfiles import StaticFiles

                app.mount(
                    "/static",
                    StaticFiles(directory=str(path / "static")),
                    name="plugin_static",
                )
            from app.plugins.skills import load_plugin_skills

            api.skills = load_plugin_skills(path, meta["id"])
            return {"meta": meta, "api": api, "mount": Mount("/" + meta["id"], app=app)}
        except Exception:
            api.dispose()
            raise

    def list(self) -> list[dict]:
        with self._lock:
            return [
                {
                    **row,
                    "id": plugin_id,
                    "running": plugin_id in self._active,
                    "error": self._errors.get(plugin_id, ""),
                    "skills": [
                        {
                            "id": skill.id,
                            "name": skill.name,
                            "description": skill.description,
                        }
                        for skill in self._active[plugin_id]["api"].skills
                    ]
                    if plugin_id in self._active
                    else [],
                    "tools": [
                        {
                            "id": definition["id"],
                            "name": definition["name"],
                            "description": definition["description"],
                        }
                        for definition, _ in self._active[plugin_id][
                            "api"
                        ].tools.values()
                    ]
                    if plugin_id in self._active
                    else [],
                    "pages": self._active[plugin_id]["api"].pages
                    if plugin_id in self._active
                    else [],
                }
                for plugin_id, row in sorted(self._rows.items())
            ]

    def skill_packages(self) -> list:
        """Live skills in the server; enabled package metadata in an offline CLI."""
        from app.plugins.skills import load_plugin_skills

        # Do not hold the runtime lock while a caller holds the store lock.
        if self._runtime_started:
            instances = tuple(self._active.values())
            return [skill for instance in instances for skill in instance["api"].skills]
        skills = []
        for row in tuple(self._rows.values()):
            if row.get("enabled") and row.get("path"):
                try:
                    skills.extend(load_plugin_skills(Path(row["path"]), row["id"]))
                except (ValueError, OSError):
                    logger.warning("Could not read plugin skills for %s", row["id"])
        return skills

    def install(self, spec: str, subdirectory: str = "", ref: str = "main") -> dict:
        from app.plugins.catalogs import prepare_install

        source, origin, expected, download = prepare_install(
            spec, self.root, subdirectory, ref
        )
        try:
            meta = self.manifest(source)
            if expected and any(
                meta[key] != expected[key] for key in ("id", "version", "sdk_version")
            ):
                raise ValueError(
                    "Plugin identity or version differs from the catalog; refresh it first"
                )
            with self._lock:
                if meta["id"] in self._rows:
                    raise ValueError("Plugin already installed; use reload")
                self._rows[meta["id"]] = {
                    **meta,
                    **origin,
                    "path": str(source),
                    "enabled": False,
                }
                try:
                    self._save()
                except Exception:
                    self._rows.pop(meta["id"])
                    raise
                return next(row for row in self.list() if row["id"] == meta["id"])
        except Exception:
            if download:
                import shutil

                shutil.rmtree(download)
            raise

    def change(self, plugin_id: str, action: str) -> dict:
        if action not in {"enable", "disable", "reload", "uninstall"}:
            raise ValueError("Unknown plugin action")
        with self._lock:
            row = self._rows.get(plugin_id)
            if row is None:
                raise KeyError(plugin_id)
            if action in {"enable", "reload"} and row.get("source") in {
                "builtin",
                "official-pending",
            }:
                row = self._move_official_source(plugin_id)
            if self._busy.get(plugin_id, 0):
                raise RuntimeError(
                    "Plugin is in use; retry after the request or tool finishes"
                )
            old = self._active.get(plugin_id)
            new = None
            if action in {"enable", "reload"}:
                self._runtime_started = True
                new = self._build(Path(row["path"]))
                if new["meta"]["id"] != plugin_id:
                    new["api"].dispose()
                    raise ValueError("Plugin id cannot change on reload")
            previous = dict(row)
            if action == "uninstall":
                self._rows.pop(plugin_id)
            else:
                self._rows[plugin_id] = {
                    **row,
                    **(new["meta"] if new else {}),
                    "enabled": new is not None,
                }
            try:
                self._save()
            except Exception:
                self._rows[plugin_id] = previous
                if new:
                    new["api"].dispose()
                raise
            if new:
                self._active[plugin_id] = new
            else:
                self._active.pop(plugin_id, None)
            self._errors.pop(plugin_id, None)
            if old:
                old["api"].dispose()
            return {
                "id": plugin_id,
                "enabled": new is not None,
                "running": new is not None,
            }

    @contextmanager
    def lease(self, plugin_id: str):
        with self._lock:
            instance = self._active.get(plugin_id)
            if instance is None:
                raise KeyError(plugin_id)
            self._busy[plugin_id] = self._busy.get(plugin_id, 0) + 1
        try:
            yield instance
        finally:
            with self._lock:
                self._busy[plugin_id] -= 1

    def definitions(self) -> dict[str, dict]:
        with self._lock:
            return {
                name: definition
                for instance in self._active.values()
                for name, (definition, _) in instance["api"].tools.items()
            }

    def execute(self, name: str, arguments: dict) -> str:
        plugin_id = name.split("__", 2)[1]
        try:
            with self.lease(plugin_id) as instance:
                _, handler = instance["api"].tools[name]
                result = handler(arguments)
                return result if isinstance(result, str) else json.dumps(result)
        except Exception as exc:
            return f"Error: plugin tool '{name}' failed: {exc}"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        with self._lock:
            matches = [
                (key, inst["mount"].matches(scope))
                for key, inst in self._active.items()
            ]
        for plugin_id, (match, child_scope) in matches:
            if match == Match.FULL:
                lease = self.lease(plugin_id)
                try:
                    instance = lease.__enter__()
                except KeyError:
                    break
                try:
                    scope.update(child_scope)
                    await instance["mount"].handle(scope, receive, send)
                finally:
                    lease.__exit__(None, None, None)
                return
        await JSONResponse({"detail": "Plugin unavailable"}, status_code=404)(
            scope, receive, send
        )

    def on_turn_end(self, context) -> None:
        with self._lock:
            plugin_ids = list(self._active)
        for plugin_id in plugin_ids:
            lease = self.lease(plugin_id)
            try:
                instance = lease.__enter__()
            except KeyError:
                continue
            try:
                for callback in instance["api"]._turn_end:
                    try:
                        callback(context)
                    except Exception:
                        logger.exception("Plugin %s turn-end hook failed", plugin_id)
            finally:
                lease.__exit__(None, None, None)

    def close(self):
        with self._lock:
            for instance in self._active.values():
                instance["api"].dispose()
            self._active.clear()


_manager: PluginManager | None = None
_manager_lock = threading.RLock()


def get_manager() -> PluginManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            from app.core.config import TOMO_HOME

            _manager = PluginManager(TOMO_HOME)
        return _manager
