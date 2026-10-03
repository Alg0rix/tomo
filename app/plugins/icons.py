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


def clean_kanji(value) -> str:
    """Return one CJK ideograph for a room tile, or "" when unusable."""
    text = str(value or "").strip()
    return text if len(text) == 1 and "\u3400" <= text <= "\u9fff" else ""
