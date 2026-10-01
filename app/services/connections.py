"""HTTP adapter over generic secret bundles; no service-specific workflows."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.services import secret_store, store
from app.services.connection_forms import preset_form, relative_path, validate_auth
from app.services.secret_forms import validate_form


def validate_config(data: dict[str, Any], *, saving: bool = False) -> dict[str, Any]:
    name = data.get("name")
    if not isinstance(name, str) or not secret_store.NAME.fullmatch(name):
        raise ValueError("A valid bundle name is required")
    base_url = data.get("base_url", "")
    if (
        not isinstance(base_url, str)
        or len(base_url) > 2048
        or any(c.isspace() or ord(c) < 32 for c in base_url)
    ):
        raise ValueError("Invalid base URL")
    try:
        url = urlsplit(base_url)
        _ = url.port
        normalized = str(httpx.URL(base_url)).rstrip("/")
    except (ValueError, httpx.InvalidURL):
        raise ValueError("Invalid base URL") from None
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError(
            "Base URL must be an HTTP(S) origin only, without credentials, path or query"
        )
    raw_form = data.get("form")
    if raw_form is None:
        kind, field = data.get("auth_type", "bearer"), data.get("auth_field", "")
        if not isinstance(kind, str) or not isinstance(field, str):
            raise ValueError("Invalid HTTP auth configuration")
        raw_form = preset_form(name, kind, field)
    if not isinstance(raw_form, dict):
        raise ValueError("Expected a metadata-only form object")
    form = validate_form({k: v for k, v in raw_form.items() if k != "auth"})
    auth = validate_auth(raw_form.get("auth"), form)
    allow_http = data.get("allow_http") is True
    if saving and url.scheme == "http" and not allow_http:
        raise ValueError(
            "HTTP sends credentials unencrypted; explicitly approve insecure HTTP or use HTTPS"
        )
    return {
        "name": name,
        "form": form,
        "usage": {
            "type": "http",
            "base_url": normalized,
            "auth": auth,
            "allow_http": allow_http,
        },
    }


def public_connection(bundle: dict[str, Any]) -> dict[str, Any]:
    usage = bundle["usage"]
    auth = usage["auth"]
    return {
        **bundle,
        "base_url": usage["base_url"],
        "auth_type": auth["type"],
        "auth_field": next(iter(auth["fields"]))
        if auth["type"] in {"header", "json"}
        else "Authorization",
    }


def list_connections(session_id: str, user_id: str) -> list[dict[str, Any]]:
    return [
        public_connection(b)
        for b in secret_store.list_bundles(session_id, user_id)
        if b["usage"].get("type") == "http"
    ]


def create_request(token: str, data: dict[str, Any]) -> dict[str, Any]:
    config = validate_config(data)
    scope = secret_store.capability_scope(token)
    if scope and scope.get("workplace_id"):
        wid = scope["workplace_id"]
        config["usage"]["workplace_id"] = wid
        wp = store.get_workplace(wid)
        config["usage"]["workplace_name"] = (wp or {}).get("name") or wid
    return secret_store.create_request(
        token, {"name": config["name"], "form": config["form"]}, usage=config["usage"]
    )


def resolve_request(
    pid: str, session_id: str, user_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    pending = secret_store.pending_request(pid)
    if not pending or pending["session_id"] != session_id:
        raise KeyError(pid)
    usage = pending["usage"]
    if data.get("cancel") is not True and usage.get("type") == "http":
        config = validate_config(
            {
                "name": pending["name"],
                "base_url": data.get("base_url", usage["base_url"]),
                "form": {**pending["form"], "auth": usage["auth"]},
                "allow_http": data.get("allow_http", False),
            },
            saving=True,
        )
        target = {
            key: usage[key]
            for key in ("workplace_id", "workplace_name")
            if key in usage
        }
        usage = {**config["usage"], **target}
    # The shared submit route also handles store-only forms, without HTTP requirements.
    result = secret_store.resolve_request(pid, session_id, user_id, data, usage=usage)
    if result.get("bundle") and usage.get("type") == "http":
        result["connection"] = public_connection(result["bundle"])
    return result


def _contains_secret(text: str, secret: str) -> bool:
    variants = {
        secret,
        quote(secret, safe=""),
        json.dumps(secret)[1:-1],
        base64.b64encode(secret.encode()).decode(),
    }
    return any(value and value in text for value in variants)


async def execute_http(scope: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    row = secret_store.scoped_bundle(scope, data.get("connection"))
    usage = json.loads(row["usage_json"])
    if usage.get("type") != "http":
        raise ValueError(
            "This bundle has no approved HTTP usage; secure storage is not an execution grant"
        )
    if usage.get("workplace_id") != scope.get("workplace_id"):
        raise ValueError(
            "Connection approved for a different execution location; request approval here"
        )
    method = data.get("method", "GET")
    if not isinstance(method, str) or method.upper() not in {
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "HEAD",
        "OPTIONS",
    }:
        raise ValueError("Unsupported HTTP method")
    path = relative_path(data.get("path", "/"))
    base = httpx.URL(usage["base_url"])
    try:
        url = httpx.URL(usage["base_url"] + path)
    except httpx.InvalidURL:
        raise ValueError("Invalid request path") from None
    if (url.scheme, url.host, url.port) != (base.scheme, base.host, base.port):
        raise ValueError("Request cannot change the approved origin")
    if base.scheme == "http" and not usage["allow_http"]:
        raise ValueError("Insecure HTTP was not approved")
    headers = data.get("headers") or {}
    if not isinstance(headers, dict) or any(
        not isinstance(k, str)
        or k.lower() not in {"accept", "content-type"}
        or not isinstance(v, str)
        or len(v) > 1024
        or any(c in v for c in ("\r", "\n", "\x00"))
        for k, v in headers.items()
    ):
        raise ValueError("Only Accept and Content-Type request headers are supported")
    body = data.get("body")
    if body is not None and (
        not isinstance(body, str) or len(body.encode()) > 1_000_000
    ):
        raise ValueError("Request body must be text, at most 1 MB")
    try:
        timeout = float(data.get("timeout", 30))
        if not 0 < timeout <= 60:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Timeout must be between 0 and 60 seconds") from None
    values = secret_store.runtime_values(row)
    auth = usage["auth"]
    protected = list(values.values())
    headers = dict(headers)
    if auth["type"] == "basic":
        username, password = (
            values[auth["username_field"]],
            values[auth["password_field"]],
        )
        if ":" in username:
            raise ValueError("Basic-auth usernames cannot contain a colon")
        encoded = base64.b64encode((username + ":" + password).encode()).decode()
        protected.append(encoded)
        headers["Authorization"] = "Basic " + encoded
    elif auth["type"] == "bearer":
        headers["Authorization"] = "Bearer " + values[auth["token_field"]]
    elif auth["type"] == "header":
        headers.update(
            {
                destination: values[source]
                for destination, source in auth["fields"].items()
            }
        )
    else:
        try:
            payload = json.loads(body or "{}")
            if not isinstance(payload, dict):
                raise ValueError
        except (ValueError, RecursionError):
            raise ValueError("JSON-field auth requires a JSON object body") from None
        payload.update(
            {
                destination: values[source]
                for destination, source in auth["fields"].items()
            }
        )
        body = json.dumps(payload)
        headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
        headers["Content-Type"] = "application/json"
    try:
        transport = None
        if scope.get("workplace_id"):
            from app.services.secret_tunnel import HTTPTransport

            transport = HTTPTransport(scope, timeout)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            async with client.stream(
                method.upper(),
                url,
                headers=headers,
                content=body.encode() if body is not None else None,
            ) as response:
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 2_000_000:
                        return {
                            "status_code": 502,
                            "body": "Response exceeds the 2 MB limit",
                        }
                text = chunks.decode("utf-8", errors="replace")
                if any(_contains_secret(text, value) for value in protected if value):
                    return {
                        "status_code": 502,
                        "body": "Response withheld: upstream echoed a private value",
                    }
                if 300 <= response.status_code < 400:
                    return {
                        "status_code": response.status_code,
                        "body": "Redirect refused; credentials were not forwarded",
                    }
                return {"status_code": response.status_code, "body": text}
    except (httpx.HTTPError, ValueError):
        return {
            "status_code": 502,
            "body": "Upstream request failed (connection, TLS or timeout); a mutation may have completed, verify before retrying",
        }
