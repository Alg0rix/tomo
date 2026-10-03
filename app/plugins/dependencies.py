"""Declared plugin requirements and persistent, additive uv environments."""

from __future__ import annotations

import importlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def requirements(path: Path) -> list[Requirement]:
    file = path / "requirements.txt"
    if file.is_file():
        if not file.resolve().is_relative_to(path.resolve()):
            raise ValueError("Dependency file must stay inside the plugin")
        if file.stat().st_size > 64_000:
            raise ValueError("Dependency file is too large")
        values = [
            line.strip()
            for line in file.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    elif (path / "pyproject.toml").is_file():
        file = path / "pyproject.toml"
        if (
            not file.resolve().is_relative_to(path.resolve())
            or file.stat().st_size > 64_000
        ):
            raise ValueError(
                "Dependency file must stay inside the plugin and fit 64 KB"
            )
        values = (
            tomllib.loads(file.read_text()).get("project", {}).get("dependencies", [])
        )
    else:
        values = []
    if not isinstance(values, list) or len(values) > 200:
        raise ValueError("Declare at most 200 Python requirements")
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Dependencies must be PEP 508 requirement strings")
        req = Requirement(value)
        if req.url:
            raise ValueError(
                "Dependencies must use registry packages, not URLs or local paths"
            )
        result.append(req)
    return result


def installed(paths=None) -> dict[str, str]:
    return {
        canonicalize_name(d.metadata["Name"]): d.version
        for d in metadata.distributions(path=sys.path if paths is None else paths)
        if d.metadata["Name"]
    }


def status(path: Path) -> dict:
    try:
        declared = requirements(path)
        versions = installed() if declared else {}
        missing = [
            str(req)
            for req in declared
            if (not req.marker or req.marker.evaluate())
            and (
                canonicalize_name(req.name) not in versions
                or not req.specifier.contains(
                    versions[canonicalize_name(req.name)], prereleases=True
                )
            )
        ]
        return {
            "status": "missing" if missing else "ready",
            "requirements": [str(req) for req in declared],
            "missing": missing,
        }
    except (ValueError, OSError) as exc:
        return {
            "status": "invalid",
            "requirements": [],
            "missing": [],
            "error": str(exc),
        }


class DependencyEnvironment:
    def __init__(self, root: Path):
        self.root = root / "plugins/dependencies"
        self.state = self.root / "active.json"
        self.lock = threading.Lock()
        self.active: str | None = None
        self.error = ""
        self.restore()

    def restore(self):
        if not self.state.exists():
            return
        try:
            row = json.loads(self.state.read_text())
            path = (self.root / row["directory"]).resolve()
            if not path.is_relative_to(self.root.resolve()) or not path.is_dir():
                raise ValueError("Plugin dependency environment is missing")
            if row["python"] != list(sys.version_info[:2]):
                raise ValueError("Python changed; sync plugin dependencies again")
            core = installed([p for p in sys.path if p != self.active])
            for name, version in row["resolved"].items():
                if name in core and core[name] != version:
                    raise ValueError(
                        "Tomo packages changed; sync plugin dependencies again"
                    )
            self._attach(path)
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
            self.error = str(exc)

    def _attach(self, path: Path):
        if self.active and self.active in sys.path:
            sys.path.remove(self.active)
        self.active = str(path)
        # Core always has import precedence. Do not execute wheel .pth files.
        sys.path.append(self.active)
        importlib.invalidate_caches()
        self.error = ""

    def prepare(self, paths: list[Path]) -> dict:
        declared = sorted({str(req) for path in paths for req in requirements(path)})
        uv = shutil.which("uv")
        if not uv:
            candidate = Path.home() / ".local/bin/uv"
            uv = str(candidate) if candidate.is_file() else None
        if not uv:
            raise ValueError("uv is unavailable; install uv on the Tomo server")
        self.root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="env-", dir=self.root))
        try:
            # Protect core and imported packages; unused overlay packages may upgrade.
            versions = installed()
            base_paths = [p for p in sys.path if p != self.active]
            core = installed(base_paths)
            imported = {
                canonicalize_name(name)
                for module, names in metadata.packages_distributions().items()
                if module in sys.modules
                for name in names
            }
            protected = {
                name: version
                for name, version in versions.items()
                if name in core or name in imported
            }
            constraint = directory / "constraints.txt"
            constraint.write_text(
                "\n".join(
                    f"{name}=={version}" for name, version in sorted(protected.items())
                )
                + "\n"
            )
            inputs = directory / "requirements.in"
            inputs.write_text("\n".join(declared) + "\n")
            lock = directory / "requirements.lock"
            common = [
                "--python",
                sys.executable,
                "--only-binary",
                ":all:",
                "--no-config",
                "--no-python-downloads",
            ]
            self._run(
                [
                    uv,
                    "pip",
                    "compile",
                    str(inputs),
                    "-c",
                    str(constraint),
                    "-o",
                    str(lock),
                    "--no-header",
                    "--no-annotate",
                    *common,
                ]
            )
            resolved = {}
            extras = []
            for line in lock.read_text().splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                req = Requirement(line)
                name = canonicalize_name(req.name)
                version = next(iter(req.specifier)).version
                resolved[name] = version
                if name not in core:
                    extras.append(line)
            wheels = directory / "overlay.lock"
            wheels.write_text("\n".join(extras) + "\n")
            site = directory / "site-packages"
            site.mkdir()
            if extras:
                self._run(
                    [
                        uv,
                        "pip",
                        "install",
                        "--target",
                        str(site),
                        "--no-deps",
                        "-r",
                        str(wheels),
                        *common,
                    ]
                )
            return {
                "directory": str(site.relative_to(self.root)),
                "python": list(sys.version_info[:2]),
                "resolved": resolved,
                "requirements": declared,
            }
        except Exception:
            shutil.rmtree(directory)
            raise

    @staticmethod
    def _run(command):
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=300
            )
        except subprocess.TimeoutExpired:
            raise ValueError(
                "Dependency sync timed out; retry when the package index is available"
            ) from None
        if result.returncode:
            raise ValueError(
                "Dependency sync failed. Existing versions are preserved; resolve conflicting requirements.\n"
                + (result.stderr or result.stdout)[-6000:]
            )

    def publish(self, row: dict):
        temporary = self.state.with_suffix(".tmp")
        temporary.write_text(json.dumps(row, indent=2))
        temporary.replace(self.state)
        self._attach(self.root / row["directory"])

    def discard(self, row: dict):
        shutil.rmtree((self.root / row["directory"]).parent)


def sync_registered(root: Path) -> dict:
    registry = root / "plugins/registry.json"
    if not registry.exists():
        return {"synced": False}
    rows = json.loads(registry.read_text())
    paths = [Path(row["path"]) for row in rows.values() if row.get("path")]
    if not any(requirements(path) for path in paths):
        return {"synced": False}
    environment = DependencyEnvironment(root)
    with environment.lock:
        candidate = environment.prepare(paths)
        try:
            environment.publish(candidate)
        except Exception:
            environment.discard(candidate)
            raise
    return {"synced": True, "requirements": candidate["requirements"]}


if __name__ == "__main__":
    try:
        print(json.dumps(sync_registered(Path(sys.argv[1]))))
    except Exception as exc:
        print("Plugin dependency restore failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
