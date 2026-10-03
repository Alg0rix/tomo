"""Supported Lucide plugin icons, rendered locally from vendored SVG paths."""

import json
from pathlib import Path

ICONS = json.loads(
    (Path(__file__).resolve().parents[1] / "static/vendor/lucide/icons.json").read_text()
)


def validate_icon(value: str) -> str:
    if not isinstance(value, str) or value not in ICONS:
        raise ValueError("Plugin icon must be one of: " + ", ".join(ICONS))
    return value
