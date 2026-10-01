"""HTTP consumer policy. The underlying secret form/store is protocol-neutral."""

from __future__ import annotations

from typing import Any

from app.services.secret_forms import FIELD_NAME


def relative_path(path: Any) -> str:
    if (
        not isinstance(path, str)
        or len(path) > 8192
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or "#" in path
        or any(c.isspace() or ord(c) < 32 for c in path)
    ):
        raise ValueError(
            "Use an origin-relative path beginning with one slash, not a URL"
        )
    return path


def preset_form(name: str, kind: str, destination: str = "") -> dict[str, Any]:
    fields = [{"name": "secret", "label": "Credential", "type": "password"}]
    auth: dict[str, Any] = {"type": kind}
    if kind == "bearer":
        auth["token_field"] = "secret"
    elif kind in {"header", "json"}:
        auth["fields"] = {destination: "secret"}
    elif kind == "basic":
        fields = [
            {"name": "username", "label": "Username", "type": "text"},
            {"name": "password", "label": "Password", "type": "password"},
        ]
        auth.update(username_field="username", password_field="password")
    else:
        raise ValueError("HTTP auth must be bearer, header, basic or json")
    return {"title": "Connect " + name, "purpose": "", "fields": fields, "auth": auth}


def validate_auth(raw: Any, form: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        raise ValueError("Declare how HTTP authentication references private fields")
    kind = raw["type"]
    names = {f["name"] for f in form["fields"]}
    if kind in {"bearer", "basic"}:
        keys = (
            ["token_field"]
            if kind == "bearer"
            else ["username_field", "password_field"]
        )
        if set(raw) != {"type", *keys} or any(
            not isinstance(raw.get(k), str) or raw[k] not in names for k in keys
        ):
            raise ValueError("HTTP auth must reference declared private fields")
        if kind == "basic" and raw[keys[0]] == raw[keys[1]]:
            raise ValueError(
                "Basic auth requires separate username and password fields"
            )
        return dict(raw)
    if kind not in {"header", "json"} or set(raw) != {"type", "fields"}:
        raise ValueError("HTTP auth must be bearer, header, basic or json")
    mapping = raw.get("fields")
    if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 12:
        raise ValueError(
            "Auth bindings must map 1–12 headers/JSON keys to private fields"
        )
    seen = set()
    for destination, source in mapping.items():
        if (
            not isinstance(destination, str)
            or not FIELD_NAME.fullmatch(destination)
            or not isinstance(source, str)
            or source not in names
        ):
            raise ValueError("HTTP auth must reference declared private fields")
        if kind == "header":
            if destination.lower() in {
                "host",
                "connection",
                "content-length",
                "content-type",
                "transfer-encoding",
                "accept",
                "upgrade",
                "proxy-authorization",
                "proxy-connection",
            }:
                raise ValueError("This header cannot be used for authentication")
            if destination.lower() in seen:
                raise ValueError("Duplicate authentication header")
            seen.add(destination.lower())
    return {"type": kind, "fields": dict(mapping)}
