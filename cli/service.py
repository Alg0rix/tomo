"""systemd --user helpers for managed Tomo installs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cli.paths import unit_path
from cli.unit import render_user_unit


def systemctl_user(
    *args: str, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def write_unit_file(home: Path | None = None) -> Path:
    path = unit_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_user_unit(), encoding="utf-8")
    return path


def service_action(action: str) -> int:
    if action not in {"status", "start", "stop", "restart"}:
        raise ValueError(action)
    proc = systemctl_user(action, "tomo")
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return int(proc.returncode)
