"""``tomo update`` — sync managed install from git and restart service."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from cli.git_sync import sync_to_origin
from cli.paths import install_dir, read_tracked_branch
from cli.service import systemctl_user


def _uv_sync(cwd: Path) -> int:
    uv = shutil.which("uv")
    if not uv:
        print("✗ uv not found on PATH", file=sys.stderr)
        return 1
    proc = subprocess.run([uv, "sync"], cwd=cwd)
    return int(proc.returncode)


def cmd_update(*, assume_yes: bool = False, home: Path | None = None) -> int:
    app = install_dir(home)
    if not (app / ".git").is_dir():
        print(f"✗ No managed install at {app}")
        print("  Run scripts/install.sh first.")
        return 1

    branch = read_tracked_branch(app)
    try:
        result = sync_to_origin(app, branch, assume_yes=assume_yes)
    except RuntimeError as exc:
        print(f"✗ Update failed: {exc}", file=sys.stderr)
        return 1

    uv_code = _uv_sync(app)
    if uv_code != 0:
        print("✗ uv sync failed", file=sys.stderr)
        return uv_code

    restart = systemctl_user("restart", "tomo")
    if restart.returncode != 0:
        print("⚠ Could not restart tomo.service (is the user unit installed?)")
        if restart.stderr.strip():
            print(f"  {restart.stderr.strip()}")

    if result.updated:
        print(f"✓ Updated to {result.head} ({result.commits} commit(s))")
    else:
        print(f"✓ Already up to date ({result.head})")
    return 0
