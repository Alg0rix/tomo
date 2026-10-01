"""Secure-input CLI and HTTP consumer client. Never reads private values."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import sys
import time
from urllib.parse import urlsplit

import httpx


def add_parser(sub: argparse._SubParsersAction) -> None:
    secret = sub.add_parser(
        "secret", help="Request arbitrary private fields through a dynamic secure form"
    )
    secret_actions = secret.add_subparsers(dest="secret_cmd", required=True)
    secret_actions.add_parser(
        "list", help="List bundle handles and field definitions, never values"
    )
    secret_revoke = secret_actions.add_parser(
        "revoke", help="Delete a private bundle in this session"
    )
    secret_revoke.add_argument("name")
    secret_request = secret_actions.add_parser(
        "request", help="Ask for any private fields; no protocol or connection required"
    )
    secret_request.add_argument("name", help="Session-local bundle name")
    secret_request.add_argument(
        "--form",
        required=True,
        help="Metadata-only JSON form schema, or - for stdin; never include private values",
    )
    secret_request.add_argument(
        "--wait",
        type=float,
        default=90,
        help="Seconds to wait, max 95; use bash timeout=120",
    )

    connection = sub.add_parser(
        "connection",
        help="Request credentials through a secure chat form; list metadata",
    )
    actions = connection.add_subparsers(dest="connection_cmd", required=True)
    actions.add_parser(
        "list", help="List this session's connections (no credential values)"
    )
    revoke = actions.add_parser("revoke", help="Delete a connection in this session")
    revoke.add_argument("name", help="Connection name or ID")
    request = actions.add_parser(
        "request", help="Open a secure form in the current Tomo web chat"
    )
    request.add_argument("name", help="Session-local connection name")
    request.add_argument(
        "--url",
        required=True,
        help="Proposed HTTP(S) origin, e.g. https://api.example",
    )
    request.add_argument(
        "--auth", choices=["bearer", "header", "basic", "json"], default="bearer"
    )
    request.add_argument(
        "--auth-field",
        default="",
        help="Header name or top-level JSON field (not the secret)",
    )
    request.add_argument(
        "--form",
        help="Dynamic field definitions plus HTTP auth bindings as JSON, or - for stdin",
    )
    request.add_argument(
        "--wait",
        type=float,
        default=90,
        help="Seconds to wait for the secure form, max 95; bash timeout must be longer",
    )

    http = sub.add_parser(
        "http",
        help="Send an authenticated HTTP request without exposing the credential",
    )
    http.add_argument("path", help="Origin-relative path, not a URL")
    http.add_argument(
        "--connection",
        required=True,
        help="Approved connection name or ID in this chat session",
    )
    http.add_argument(
        "-X",
        "--request",
        dest="method",
        default=None,
        help="HTTP method (default GET, or POST with -d)",
    )
    http.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        help="Accept or Content-Type header; repeatable",
    )
    http.add_argument(
        "-d",
        "--data",
        default=None,
        help="Text/JSON body; use - for stdin (no @file expansion)",
    )
    http.add_argument(
        "--timeout", type=float, default=30, help="Upstream timeout, 0–60 seconds"
    )


@contextmanager
def _client():
    base = os.environ.get("TOMO_BROKER_URL") or os.environ.get("TOMO_HTTP_BROKER", "")
    token = os.environ.get("TOMO_BROKER_TOKEN") or os.environ.get("TOMO_HTTP_TOKEN", "")
    try:
        url = urlsplit(base)
        valid = (
            url.scheme == "http"
            and url.hostname in {"127.0.0.1", "::1", "localhost"}
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and url.path in {"", "/"}
        )
    except ValueError:
        valid = False
    if not valid or not token:
        raise ValueError(
            "Run from a foreground local Tomo web-chat bash tool; broker access is session-scoped"
        )
    with httpx.Client(
        base_url=base,
        headers={"Authorization": "Bearer " + token},
        timeout=65,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        yield client


def _response(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        raise ValueError("Unexpected broker response") from None
    if not response.is_success:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise ValueError(detail if isinstance(detail, str) else "Broker request failed")
    if not isinstance(data, dict):
        raise ValueError("Unexpected broker response")
    return data


def run(args: argparse.Namespace) -> int:
    try:
        with _client() as client:
            if args.cmd in {"connection", "secret"}:
                generic = args.cmd == "secret"
                action = args.secret_cmd if generic else args.connection_cmd
                prefix = "/api/secret-broker" if generic else "/api/connection-broker"
                resource = "/bundles" if generic else "/connections"
                if action == "list":
                    data = _response(client.get(prefix + resource))
                elif action == "revoke":
                    from urllib.parse import quote

                    data = _response(
                        client.delete(
                            prefix + resource + "/" + quote(args.name, safe="")
                        )
                    )
                else:
                    if not 0 < args.wait <= 95:
                        raise ValueError("--wait must be between 0 and 95 seconds")
                    definition = {"name": args.name}
                    if not generic:
                        definition.update(
                            base_url=args.url,
                            auth_type=args.auth,
                            auth_field=args.auth_field,
                        )
                    if args.form is not None:
                        raw = sys.stdin.read(65_537) if args.form == "-" else args.form
                        try:
                            if len(raw.encode()) > 65_536:
                                raise ValueError
                            definition["form"] = json.loads(raw)
                        except (ValueError, RecursionError):
                            raise ValueError(
                                "--form must be a metadata-only JSON object within 64 KB"
                            ) from None
                    pending = _response(
                        client.post(prefix + "/requests", json=definition)
                    )
                    print(
                        "Waiting for the secure input form in this chat…",
                        file=sys.stderr,
                        flush=True,
                    )
                    deadline = time.monotonic() + args.wait
                    while True:
                        data = _response(
                            client.get(prefix + "/requests/" + pending["id"])
                        )
                        if data.get("status") == "ready":
                            break
                        if data.get("status") != "pending":
                            raise ValueError("Secure input cancelled")
                        if time.monotonic() >= deadline:
                            raise ValueError(
                                "Secure input timed out; request the form again (never paste private values into chat)"
                            )
                        time.sleep(0.5)
                print(json.dumps(data, ensure_ascii=False))
                return 0
            headers = {}
            for raw in args.header:
                key, sep, value = raw.partition(":")
                if not sep:
                    raise ValueError("Use -H 'Content-Type: application/json'")
                headers[key.strip()] = value.strip()
            body = sys.stdin.read(1_000_001) if args.data == "-" else args.data
            result = _response(
                client.post(
                    "/api/connection-broker/http",
                    json={
                        "connection": args.connection,
                        "method": args.method
                        or ("POST" if body is not None else "GET"),
                        "path": args.path,
                        "headers": headers,
                        "body": body,
                        "timeout": args.timeout,
                    },
                )
            )
            print(result["body"])
            if result["status_code"] >= 300:
                print(
                    "Upstream HTTP status: " + str(result["status_code"]),
                    file=sys.stderr,
                )
                return 22
            return 0
    except ValueError as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 1
    except httpx.HTTPError:
        print("Error: could not reach the Tomo connection broker", file=sys.stderr)
        return 1
