"""Remove managed Tomo install and optional data trees."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cli.paths import cli_symlink_path, default_tomo_home, default_tomo_work, install_dir
from cli.service import systemctl_user
from cli.unit import parse_tomo_paths_from_unit


def _under_home(path: Path, home: Path) -> bool:
    try:
        path.resolve().relative_to(home.resolve())
        return True
    except ValueError:
        return False


def uninstall(
    *,
    purge: bool,
    assume_yes: bool,
    home: Path | None = None,
    input_fn: Callable[[str], str] | None = None,
    run_systemctl: Callable[..., Any] | None = None,
) -> None:
    user_home = Path.home() if home is None else Path(home)
    app = install_dir(user_home)
    unit = user_home / ".config" / "systemd" / "user" / "tomo.service"
    link = cli_symlink_path(user_home)
    ctl = run_systemctl or systemctl_user

    unit_text = ""
    if unit.is_file():
        unit_text = unit.read_text(encoding="utf-8")

    # Stop/disable — ignore failures when unit missing
    try:
        ctl("stop", "tomo")
    except Exception:
        pass
    try:
        ctl("disable", "tomo")
    except Exception:
        pass

    if unit.is_file():
        unit.unlink()
        try:
            ctl("daemon-reload")
        except Exception:
            pass

    if link.is_symlink() or link.exists():
        try:
            target = link.resolve() if link.exists() else None
        except OSError:
            target = None
        if target is None or _under_home(target, app) or str(target).startswith(str(app)):
            try:
                link.unlink()
            except OSError:
                pass
        elif link.is_symlink():
            # Symlink pointing into managed install even if broken
            try:
                raw = link.readlink()
                if "tomo/app" in str(raw):
                    link.unlink()
            except OSError:
                pass

    if app.exists():
        shutil.rmtree(app)
    share = app.parent  # .../tomo
    if share.is_dir() and not any(share.iterdir()):
        share.rmdir()

    if not purge:
        return

    if unit_text:
        data_home, data_work = parse_tomo_paths_from_unit(unit_text, user_home)
    else:
        data_home = default_tomo_home(user_home)
        data_work = default_tomo_work(user_home)

    for path, label in ((data_home, "TOMO_HOME"), (data_work, "TOMO_WORK")):
        if not _under_home(path, user_home):
            raise RuntimeError(
                f"Refusing to purge {label} outside user home: {path}"
            )

    if not assume_yes:
        ask = input_fn or input
        try:
            answer = (
                ask(
                    f"Delete {data_home} and {data_work}? This cannot be undone. [y/N]: "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer not in {"y", "yes"}:
            print("Purge aborted; service and code install already removed.")
            raise SystemExit(1)

    for path in (data_home, data_work):
        if path.exists():
            shutil.rmtree(path)
