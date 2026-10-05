#!/usr/bin/env python3
"""Minimal deterministic MCP fixture for Member-sandbox tests (stdlib only).

Speaks newline-delimited JSON-RPC on stdio: ``initialize``,
``tools/list`` and ``tools/call`` plus empty resource/prompt listings so the
real SDK discovery handshake also succeeds. No third-party imports, so it
runs identically on the host (Admin discovery) and inside the restricted
``tomo:sandbox`` container (Member calls).

Tools:

* ``echo`` — return ``"echo: <text>"``.
* ``add`` — return ``str(a + b)`` for numbers.
* ``write_probe`` — try writing ``content`` to ``path``; success returns
  ``"wrote <n> bytes"``, refusal raises a JSON-RPC tool error (used to
  prove kernel RO mounts inside the Member container).
* ``sleep_probe`` — sleep ``seconds`` then return ``"slept"`` (used to
  prove duration enforcement).
* ``env_probe`` — return the sorted server-process environment key list
  (used to prove configured server secrets never enter the sandbox).
"""

from __future__ import annotations

import json
import os
import sys
import time


def _result(msg_id, payload):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": payload}) + "\n")
    sys.stdout.flush()


def _error(msg_id, message):
    sys.stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": msg_id,
             "error": {"code": -32000, "message": message}}
        )
        + "\n"
    )
    sys.stdout.flush()


def _tool_result(text):
    return {"content": [{"type": "text", "text": text}]}


def _call(name, arguments):
    args = arguments if isinstance(arguments, dict) else {}
    if name == "echo":
        return _tool_result("echo: " + str(args.get("text", "")))
    if name == "add":
        return _tool_result(str(float(args.get("a", 0)) + float(args.get("b", 0))))
    if name == "write_probe":
        path = str(args.get("path", ""))
        content = str(args.get("content", "x"))
        with open(path, "w", encoding="utf-8") as handle:
            count = handle.write(content)
        return _tool_result(f"wrote {count} bytes")
    if name == "sleep_probe":
        time.sleep(min(float(args.get("seconds", 0)), 120.0))
        return _tool_result("slept")
    if name == "env_probe":
        return _tool_result(json.dumps(sorted(os.environ.keys())))
    raise ValueError(f"unknown tool: {name}")


def _describe():
    def tool(name, description):
        return {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object"},
        }

    return [
        tool("echo", "Echo text back"),
        tool("add", "Add two numbers"),
        tool("write_probe", "Attempt a file write"),
        tool("sleep_probe", "Sleep then reply"),
        tool("env_probe", "List environment keys"),
    ]


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            _result(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "tomo-member-fixture", "version": "1.0"},
            })
        elif method == "tools/list":
            _result(msg_id, {"tools": _describe()})
        elif method == "tools/call":
            try:
                _result(msg_id, _call(params.get("name"), params.get("arguments")))
            except Exception as exc:
                _error(msg_id, f"{type(exc).__name__}: {exc}"[:300])
        elif method in ("resources/list", "resources/templates/list", "prompts/list"):
            key = {"resources/list": "resources",
                   "resources/templates/list": "resourceTemplates",
                   "prompts/list": "prompts"}[method]
            _result(msg_id, {key: []})
        elif method == "ping":
            _result(msg_id, {})
        elif msg_id is not None:
            _error(msg_id, f"unknown method: {method}")


if __name__ == "__main__":
    main()
