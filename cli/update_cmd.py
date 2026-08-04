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

    # Seed missing session/admin/.secret_key for installs that predate hardening.
    try:
        from app.core.bootstrap import ensure_bootstrap_secrets
        from cli.paths import default_tomo_home

        boot = ensure_bootstrap_secrets(default_tomo_home(home))
        for note in boot.notes:
            if note.startswith("Generated"):
                print(f"→ {note}")
        if boot.created_admin_password and boot.admin_password:
            print("")
            print("Bootstrap admin password (save this — shown once):")
            print("  user:     admin")
            print(f"  password: {boot.admin_password}")
            print(f"  file:     {boot.env_path}")
            print("")
    except Exception as exc:
        print(f"⚠ Could not ensure bootstrap secrets: {exc}", file=sys.stderr)

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
