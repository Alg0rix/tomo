"""Metadata-only dynamic forms. Every submitted value stays private."""

from __future__ import annotations

import re
from typing import Any

FIELD_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Invalid form label, title or purpose")
    return value


def validate_form(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) - {"title", "purpose", "fields"}:
        raise ValueError("Secret form supports only title, purpose and fields")
    fields = raw.get("fields")
    if not isinstance(fields, list) or not 1 <= len(fields) <= 12:
        raise ValueError("Declare 1–12 private fields")
    clean = []
    names: set[str] = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) - {
            "name",
            "label",
            "type",
            "description",
            "required",
            "options",
        }:
            raise ValueError(
                "Fields support only name, label, type, description, required and options; values/defaults/public flags are forbidden"
            )
        name = field.get("name")
        kind = field.get("type", "password")
        if not isinstance(name, str) or not FIELD_NAME.fullmatch(name) or name in names:
            raise ValueError("Private field names must be valid and unique")
        if not isinstance(kind, str) or kind not in {
            "text",
            "password",
            "textarea",
            "select",
        }:
            raise ValueError(
                "Private fields must be text, password, textarea or select"
            )
        required = field.get("required", True)
        if not isinstance(required, bool):
            raise ValueError("Field required must be a boolean")
        item = {
            "name": name,
            "label": _text(field.get("label", name), 100),
            "type": kind,
            "description": _text(field.get("description", ""), 300),
            "required": required,
        }
        if kind == "select":
            options = field.get("options")
            if (
                not isinstance(options, list)
                or not 1 <= len(options) <= 32
                or any(not isinstance(o, str) or not o or len(o) > 120 for o in options)
            ):
                raise ValueError("Select fields require 1–32 non-empty text options")
            item["options"] = list(dict.fromkeys(options))
        elif "options" in field:
            raise ValueError("Only select fields can declare options")
        clean.append(item)
        names.add(name)
    return {
        "title": _text(raw.get("title", "Secure input"), 120),
        "purpose": _text(raw.get("purpose", ""), 500),
        "fields": clean,
    }


def validate_values(form: dict[str, Any], raw: Any) -> dict[str, str]:
    names = {f["name"] for f in form["fields"]}
    if not isinstance(raw, dict) or set(raw) - names:
        raise ValueError("Submit only the declared private fields")
    values = {}
    for field in form["fields"]:
        value = raw.get(field["name"], "")
        limit = 32768 if field["type"] == "textarea" else 8192
        if (
            not isinstance(value, str)
            or len(value) > limit
            or "\x00" in value
            or (field["type"] != "textarea" and any(c in value for c in ("\r", "\n")))
            or (field["required"] and not value.strip())
            or (field["type"] == "select" and value and value not in field["options"])
        ):
            raise ValueError(
                "Complete required private fields with valid values within the field size limits"
            )
        values[field["name"]] = value
    return values
