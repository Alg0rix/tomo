"""systemd user unit text for managed Tomo installs."""

from __future__ import annotations

from pathlib import Path

from cli.paths import default_tomo_home, default_tomo_work

_UNIT = """\
[Unit]
Description=Tomo agent swarm
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/.local/share/tomo/app
ExecStart=%h/.local/share/tomo/app/.venv/bin/python -m app.main
Restart=on-failure
RestartSec=5
Environment=TOMO_HOME=%h/.tomo
Environment=TOMO_WORK=%h/tomo
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def render_user_unit() -> str:
    return _UNIT


def _expand_systemd_path(value: str, home: Path) -> Path:
    v = value.strip().strip('"').strip("'")
    v = v.replace("%h", str(home))
    v = v.replace("$HOME", str(home))
    v = v.replace("${HOME}", str(home))
    return Path(v).expanduser()


def parse_tomo_paths_from_unit(text: str, home: Path) -> tuple[Path, Path]:
    home_path = default_tomo_home(home)
    work_path = default_tomo_work(home)
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("Environment="):
            continue
        payload = line.split("=", 1)[1]
        if payload.startswith("TOMO_HOME="):
            home_path = _expand_systemd_path(payload[len("TOMO_HOME=") :], home)
        elif payload.startswith("TOMO_WORK="):
            work_path = _expand_systemd_path(payload[len("TOMO_WORK=") :], home)
    return home_path, work_path
