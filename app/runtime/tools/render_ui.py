"""Declarative generative UI tool."""

from __future__ import annotations

import json
from typing import Any

from app.runtime.ui import UIValidationError, validate_ui_payload


def run(arguments: dict[str, Any]) -> str:
    try:
        payload = validate_ui_payload(arguments)
    except UIValidationError as exc:
        return f"Error: invalid UI: {exc}"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_result(value: str) -> dict[str, Any] | None:
    """Decode a successful tool result for the SSE mapper."""
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("tree") else None


__all__ = ["parse_result", "run"]
