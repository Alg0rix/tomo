"""Stable wire ordering for reusable tool definitions."""
from __future__ import annotations

import json
from typing import Any


def stable_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Canonicalize object keys and tool order without changing schema values.

    Array order inside schemas is preserved. Return fresh dictionaries so
    shared registry definitions and caller-owned lists are never mutated.
    """
    if not tools:
        return tools
    encoded = [json.dumps(tool, sort_keys=True, ensure_ascii=False) for tool in tools]
    return [json.loads(schema) for schema in sorted(encoded)]
