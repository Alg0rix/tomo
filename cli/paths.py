"""Managed-install path helpers (systemd user install layout)."""

from __future__ import annotations

from pathlib import Path

UNIT_NAME = "tomo.service"
REPO_HTTPS = "https://github.com/Alg0rix/tomo.git"
DEFAULT_BRANCH = "main"


def _home(home: Path | None) -> Path:
    return Path.home() if home is None else Path(home)


def install_dir(home: Path | None = None) -> Path:
    return _home(home) / ".local" / "share" / "tomo" / "app"


def unit_path(home: Path | None = None) -> Path:
    return _home(home) / ".config" / "systemd" / "user" / UNIT_NAME


def cli_symlink_path(home: Path | None = None) -> Path:
    return _home(home) / ".local" / "bin" / "tomo"


def default_tomo_home(home: Path | None = None) -> Path:
    return _home(home) / ".tomo"


def default_tomo_work(home: Path | None = None) -> Path:
    return _home(home) / "tomo"


def branch_marker_path(install: Path) -> Path:
    return Path(install) / ".tomo-install-branch"


def read_tracked_branch(install: Path, default: str = DEFAULT_BRANCH) -> str:
    marker = branch_marker_path(install)
    if not marker.is_file():
        return default
    text = marker.read_text(encoding="utf-8").strip()
    return text or default


def write_tracked_branch(install: Path, branch: str) -> None:
    branch_marker_path(install).write_text(branch.strip() + "\n", encoding="utf-8")
