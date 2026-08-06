"""Small, strict interface between model output and the browser UI.

The model can describe a UI tree, but it cannot submit HTML, CSS, or JavaScript
to the main document.  The browser owns the renderer and only accepts the
allow-listed node types and values below.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_NODES = 120
MAX_DEPTH = 8
MAX_STRING = 20_000
MAX_OPTIONS = 100
MAX_ROWS = 200

NODE_TYPES = frozenset(
    {
        "text",
        "markdown",
        "card",
        "stack",
        "grid",
        "badge",
        "divider",
        "table",
        "chart",
        "mermaid",
        "image",
        "link",
        "input",
        "select",
        "button",
    }
)
CONTAINER_TYPES = frozenset({"card", "stack", "grid"})
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_ACTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_PATCH_PATH_RE = re.compile(r"^/(?:tree|state)(?:/[A-Za-z0-9_.:-]+|/[0-9]+)*$")


class UIValidationError(ValueError):
    """Raised when an agent-provided UI payload is outside the contract."""


def _string(value: Any, *, field: str, limit: int = MAX_STRING) -> str:
    if not isinstance(value, str):
        raise UIValidationError(f"{field} must be a string")
    if len(value) > limit:
        raise UIValidationError(f"{field} exceeds {limit} characters")
    return value


def _optional_string(out: dict[str, Any], source: dict[str, Any], field: str) -> None:
    if field in source and source[field] is not None:
        out[field] = _string(source[field], field=field)


def _safe_id(value: Any, *, field: str) -> str:
    result = _string(value, field=field, limit=80).strip()
    if not _ID_RE.fullmatch(result):
        raise UIValidationError(f"{field} has an invalid id")
    return result


def _node(value: Any, *, depth: int, count: list[int]) -> dict[str, Any]:
    if depth > MAX_DEPTH:
        raise UIValidationError(f"UI tree exceeds depth {MAX_DEPTH}")
    if not isinstance(value, dict):
        raise UIValidationError("every UI node must be an object")
    count[0] += 1
    if count[0] > MAX_NODES:
        raise UIValidationError(f"UI tree exceeds {MAX_NODES} nodes")

    kind = _string(value.get("type"), field="node.type", limit=32).strip().lower()
    if kind not in NODE_TYPES:
        raise UIValidationError(f"unsupported UI node type: {kind or '(empty)'}")
    out: dict[str, Any] = {"type": kind}
    if "id" in value:
        out["id"] = _safe_id(value["id"], field="node.id")

    for field in (
        "title",
        "label",
        "description",
        "value",
        "placeholder",
        "action",
        "variant",
        "alt",
        "language",
        "href",
        "src",
    ):
        _optional_string(out, value, field)

    if "disabled" in value:
        if not isinstance(value["disabled"], bool):
            raise UIValidationError("node.disabled must be boolean")
        out["disabled"] = value["disabled"]

    if kind in CONTAINER_TYPES:
        children = value.get("children", [])
        if not isinstance(children, list):
            raise UIValidationError(f"{kind}.children must be an array")
        out["children"] = [
            _node(child, depth=depth + 1, count=count) for child in children
        ]
    elif "children" in value:
        raise UIValidationError(f"{kind} cannot contain children")

    if kind in {"text", "markdown", "badge", "mermaid"}:
        if "value" not in out:
            raise UIValidationError(f"{kind}.value is required")
    if kind == "button" and not out.get("label"):
        raise UIValidationError("button.label is required")
    if kind == "input" and not out.get("id"):
        raise UIValidationError("input.id is required")
    if kind == "select" and not out.get("id"):
        raise UIValidationError("select.id is required")
    if kind in {"button", "input", "select", "link"} and not out.get("action"):
        if kind != "link":
            raise UIValidationError(f"{kind}.action is required")

    if kind == "select":
        options = value.get("options", [])
        if not isinstance(options, list) or len(options) > MAX_OPTIONS:
            raise UIValidationError("select.options must be a short array")
        clean_options: list[dict[str, str]] = []
        for option in options:
            if not isinstance(option, dict):
                raise UIValidationError("select options must be objects")
            label = _string(option.get("label"), field="option.label", limit=500)
            option_value = _string(
                option.get("value"), field="option.value", limit=500
            )
            clean_options.append({"label": label, "value": option_value})
        out["options"] = clean_options

    if kind == "table":
        columns = value.get("columns", [])
        rows = value.get("rows", [])
        if not isinstance(columns, list) or not isinstance(rows, list):
            raise UIValidationError("table.columns and table.rows must be arrays")
        if len(columns) > 50 or len(rows) > MAX_ROWS:
            raise UIValidationError("table is too large")
        out["columns"] = [
            _string(column, field="table.column", limit=200) for column in columns
        ]
        clean_rows: list[list[str]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) > len(out["columns"]):
                raise UIValidationError("table rows must be arrays matching columns")
            clean_rows.append(
                [_string(cell, field="table.cell", limit=2_000) for cell in row]
            )
        out["rows"] = clean_rows

    if kind == "chart":
        data = value.get("data", [])
        if not isinstance(data, list) or len(data) > MAX_ROWS:
            raise UIValidationError("chart.data must be a short array")
        clean_data: list[dict[str, Any]] = []
        for point in data:
            if not isinstance(point, dict):
                raise UIValidationError("chart points must be objects")
            label = _string(point.get("label"), field="chart.label", limit=200)
            number = point.get("value")
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise UIValidationError("chart.value must be numeric")
            clean_data.append({"label": label, "value": number})
        out["data"] = clean_data

    return out


def validate_ui_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a ``render_ui`` tool payload."""
    if not isinstance(arguments, dict):
        raise UIValidationError("arguments must be an object")
    ui_id = arguments.get("ui_id") or arguments.get("id")
    if not ui_id:
        raise UIValidationError("ui_id is required")
    mode = arguments.get("mode", "replace")
    if mode not in {"replace", "patch"}:
        raise UIValidationError("mode must be replace or patch")
    tree = arguments.get("tree")
    patch = arguments.get("patch")
    if mode == "replace" and tree is None:
        raise UIValidationError("tree is required for replace mode")
    if mode == "replace" and patch is not None:
        raise UIValidationError("patch is only valid in patch mode")
    if mode == "patch" and tree is None and patch is None:
        raise UIValidationError("patch or tree is required for patch mode")
    result: dict[str, Any] = {
        "ui_id": _safe_id(ui_id, field="ui_id"),
        "mode": mode,
    }
    if tree is not None:
        result["tree"] = _node(tree, depth=0, count=[0])
    if patch is not None:
        if not isinstance(patch, list) or len(patch) > 50:
            raise UIValidationError("patch must be an array with at most 50 operations")
        clean_patch: list[dict[str, Any]] = []
        for operation in patch:
            if not isinstance(operation, dict):
                raise UIValidationError("patch operations must be objects")
            op = _string(operation.get("op"), field="patch.op", limit=16).lower()
            path = _string(operation.get("path"), field="patch.path", limit=200)
            if op not in {"add", "replace", "remove"} or not _PATCH_PATH_RE.fullmatch(path):
                raise UIValidationError("patch operation or path is invalid")
            clean: dict[str, Any] = {"op": op, "path": path}
            if op != "remove":
                if "value" not in operation:
                    raise UIValidationError("patch add/replace requires value")
                try:
                    encoded = json.dumps(
                        operation["value"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                except (TypeError, ValueError) as exc:
                    raise UIValidationError("patch value must be JSON data") from exc
                if len(encoded) > MAX_STRING:
                    raise UIValidationError("patch value is too large")
                clean["value"] = operation["value"]
            clean_patch.append(clean)
        result["patch"] = clean_patch
    if "state" in arguments:
        state = arguments.get("state")
        if not isinstance(state, dict):
            raise UIValidationError("state must be an object")
        try:
            encoded_state = json.dumps(
                state, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise UIValidationError("state must contain JSON values") from exc
        if len(encoded_state) > MAX_STRING:
            raise UIValidationError("state is too large")
        result["state"] = state
    return result


def validate_ui_action(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate the small, typed payload sent by an interactive widget."""
    if not isinstance(arguments, dict):
        raise UIValidationError("arguments must be an object")
    ui_id = _safe_id(arguments.get("ui_id"), field="ui_id")
    action = _string(arguments.get("action"), field="action", limit=80).strip()
    if not _ACTION_RE.fullmatch(action):
        raise UIValidationError("action has an invalid name")
    payload = arguments.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise UIValidationError("payload must be an object")
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise UIValidationError("payload must contain JSON values") from exc
    if len(encoded) > 20_000:
        raise UIValidationError("payload exceeds 20000 characters")
    return {"ui_id": ui_id, "action": action, "payload": payload}


__all__ = ["UIValidationError", "validate_ui_action", "validate_ui_payload"]
