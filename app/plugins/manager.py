"""Live plugin registry. Mutations are serialized; busy plugins cannot reload."""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time
import types
import uuid

from fastapi import Depends, FastAPI
from starlette.responses import JSONResponse
from starlette.routing import Mount, Match

from app.plugins.sdk import PluginAPI

logger = logging.getLogger(__name__)


class PluginManager:
    def __init__(self, root: Path):
        from app.plugins.dependencies import DependencyEnvironment

        self.root = root
        self.dependencies = DependencyEnvironment(root)
        self._lock = threading.RLock()
        self._active: dict[str, dict] = {}
        self._runtime_started = False
        self._busy: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._updates: dict[str, tuple[dict, dict]] = {}
        self._check_lock = threading.Lock()
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
        return PluginManager.manifest_data(row)

    @staticmethod
    def manifest_data(row: dict) -> dict:
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
        from app.plugins.dependencies import status

        dependency_status = status(path)
        if dependency_status["status"] != "ready":
            raise ValueError(
                "Plugin dependencies are not ready; use Sync dependencies. "
                + dependency_status.get(
                    "error", ", ".join(dependency_status["missing"])
                )
            )

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
        except ModuleNotFoundError as exc:
            api.dispose()
            requirement = (
                "opencv-python-headless"
                if exc.name == "cv2"
                else "the package providing " + str(exc.name)
            )
            raise ValueError(
                "Missing Python module "
                + str(exc.name)
                + ". Declare "
                + requirement
                + " in requirements.txt or pyproject.toml, then Sync dependencies and Enable."
            ) from exc
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
                    "update": self._update_status(plugin_id, row),
                    "dependencies": self.dependency_status(Path(row["path"]))
                    if row.get("path")
                    else {},
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

    def dependency_status(self, path: Path) -> dict:
        from app.plugins.dependencies import status

        result = status(path)
        if self.dependencies.error:
            result["environment_error"] = self.dependencies.error
        return result

    def sync_dependencies(self, plugin_id: str) -> dict:
        with self.dependencies.lock:
            with self._lock:
                if plugin_id not in self._rows:
                    raise KeyError(plugin_id)
                snapshot = {key: dict(row) for key, row in self._rows.items()}
                paths = [
                    Path(row["path"]) for row in snapshot.values() if row.get("path")
                ]
            prepared = self.dependencies.prepare(paths)
            try:
                with self._lock:
                    if snapshot != self._rows:
                        raise RuntimeError(
                            "Plugins changed during dependency sync; retry"
                        )
                    self.dependencies.publish(prepared)
                    self._errors.pop(plugin_id, None)
                    return {
                        "id": plugin_id,
                        "dependencies": self.dependency_status(
                            Path(snapshot[plugin_id]["path"])
                        ),
                        "enabled": snapshot[plugin_id].get("enabled", False),
                    }
            except Exception:
                self.dependencies.discard(prepared)
                raise

    def _update_status(self, plugin_id: str, row: dict) -> dict:
        cached = self._updates.get(plugin_id)
        if cached and cached[0] == row:
            return cached[1]
        return {"status": "unchecked" if row.get("source") == "git" else "local"}

    def check_updates(self, force: bool = True) -> list[dict]:
        from app.plugins.updates import check_source

        # Network calls never hold the runtime lock or import plugin code.
        with self._check_lock:
            with self._lock:
                rows = [dict(row) for row in self._rows.values()]
            results = []
            for row in rows:
                with self._lock:
                    cached = self._updates.get(row["id"])
                if (
                    not force
                    and cached
                    and cached[0] == row
                    and time.time() - cached[1]["checked_at"] < 300
                ):
                    result = cached[1]
                else:
                    try:
                        result = check_source(row, self.manifest_data)
                    except Exception as exc:
                        result = {
                            "id": row["id"],
                            "status": "error",
                            "checked_at": time.time(),
                            "message": str(exc),
                        }
                    with self._lock:
                        if self._rows.get(row["id"]) != row:
                            continue
                        self._updates[row["id"]] = (row, result)
                results.append(result)
            return results

    def update(self, plugin_id: str) -> dict:
        with self.dependencies.lock:
            return self._update(plugin_id)

    def _update(self, plugin_id: str) -> dict:
        from app.plugins.catalogs import GitSource, download_repository
        from app.plugins.updates import check_source
        from app.plugins.dependencies import status

        with self._lock:
            row = dict(self._rows[plugin_id])
            snapshot = {key: dict(value) for key, value in self._rows.items()}
            old = self._active.get(plugin_id)
            if self._busy.get(plugin_id, 0):
                raise RuntimeError("Plugin is in use; retry after its work finishes")
        candidate = check_source(row, self.manifest_data)
        if candidate["status"] in {"local", "pinned"}:
            raise ValueError(candidate["message"])
        if candidate["status"] == "current":
            with self._lock:
                if self._rows.get(plugin_id) != row:
                    raise RuntimeError("Plugin changed during update; retry")
                self._updates[plugin_id] = (row, candidate)
            return {"id": plugin_id, "updated": False, "commit": row.get("commit")}
        source = GitSource.model_validate(row["origin"])
        downloads = self.root / "plugins/sources"
        downloads.mkdir(parents=True, exist_ok=True)
        download = Path(tempfile.mkdtemp(prefix="repo-", dir=downloads))
        new = None
        prepared = None
        published = False
        try:
            download_repository(source, download, commit=candidate["latest_commit"])
            path = download / source.subdirectory
            meta = self.manifest(path)
            if meta["id"] != plugin_id:
                raise ValueError(
                    "Update plugin identity differs from installed identity"
                )
            if not (path / "plugin.py").is_file():
                raise ValueError("Update requires plugin.py")
            if row.get("enabled") and status(path)["status"] != "ready":
                paths = [
                    path if key == plugin_id else Path(value["path"])
                    for key, value in snapshot.items()
                    if value.get("path")
                ]
                prepared = self.dependencies.prepare(paths)
            with self._lock:
                if self._rows != snapshot or self._active.get(plugin_id) is not old:
                    raise RuntimeError("Plugin changed during update; retry")
                if self._busy.get(plugin_id, 0):
                    raise RuntimeError(
                        "Plugin is in use; retry after its work finishes"
                    )
                if row.get("enabled"):
                    if prepared:
                        self.dependencies.publish(prepared)
                        published = True
                    new = self._build(path)
                replacement = {
                    **row,
                    **meta,
                    "path": str(path),
                    "commit": candidate["latest_commit"],
                }
                self._rows[plugin_id] = replacement
                try:
                    self._save()
                except Exception:
                    self._rows[plugin_id] = row
                    raise
                if new:
                    self._active[plugin_id] = new
                    self._runtime_started = True
                self._updates[plugin_id] = (
                    replacement,
                    {
                        **candidate,
                        "status": "current",
                        "message": "Up to date.",
                        "installed_commit": replacement["commit"],
                        "installed_version": replacement["version"],
                    },
                )
                self._errors.pop(plugin_id, None)
                if old:
                    old["api"].dispose()
                return {
                    "id": plugin_id,
                    "updated": True,
                    "enabled": bool(row.get("enabled")),
                    "running": new is not None,
                    "commit": replacement["commit"],
                    "version": meta["version"],
                }
        except Exception as exc:
            if prepared and not published:
                self.dependencies.discard(prepared)
            if new:
                new["api"].dispose()
            shutil.rmtree(download)
            with self._lock:
                if self._rows.get(plugin_id) == row:
                    self._errors[plugin_id] = "Update failed: " + str(exc)
            raise

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
                    raise ValueError(
                        "Plugin already installed; use update for Git sources or reload for local edits"
                    )
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
        if action == "sync-dependencies":
            return self.sync_dependencies(plugin_id)
        if action == "update":
            return self.update(plugin_id)
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
