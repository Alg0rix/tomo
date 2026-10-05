"""Member MCP invocation inside the caller's per-chat restricted container.

Admin MCP calls use a persistent host subprocess/HTTP session (see
``manager.McpConnectionManager``). Members never share that host authority:
every Member stdio tool call runs the server command **inside the caller's
own per-chat container** via :meth:`ContainerBackend.execute`, which already
provides owner-scoped mounts (kernel RO), resource limits,
duration/process-tree supervision, generation-gated revocation and confirmed
teardown. There is deliberately no persistent Member session to keep alive.

Wire format: MCP stdio transports speak newline-delimited JSON-RPC, so the
in-container runner below needs only the standard library — no MCP SDK in
the sandbox image. The server entrypoint script is staged **by content**
(host reads the Admin-configured script file, bounded) rather than by host
path, because host paths do not exist inside the container.

Fail-closed rules (no silent downgrade to host execution):

* Only ``python``/``python3``/``node``/``nodejs`` entrypoints run sandboxed.
  Anything else (shell wrappers, absolute host binaries, ``-c`` shims)
  refuses with an explicit boundary error.
* The server's configured ``env``/``headers`` are **never** forwarded to a
  Member call. Server-wide tokens must not become Member-accessible; a
  server that needs secret-bearing env for its handshake fails closed for
  Members instead of inheriting it.
* Streamable HTTP Member calls run host-side (containers have no network)
  with server headers stripped, scoped-egress admission and the shared
  SSRF/private-host guard. They never touch the persistent ``_live``
  sessions.
"""

from __future__ import annotations

import json
from typing import Any

from app.runtime.access import AccessDenied, AccessUnavailable

MEMBER_INTERPRETERS = frozenset({"python", "python3", "node", "nodejs"})
_MAX_SCRIPT_BYTES = 512 * 1024
_MAX_ARGV_ITEMS = 32
_MAX_ARGV_CHARS = 4096
_DEFAULT_TIMEOUT = 30.0

# In-container runner (stdlib only). Reads one JSON bundle on stdin:
# {interpreter, script, script_name, extra_argv, tool, arguments}.
# Stages the script under container-local tmp (never a persisted mount),
# performs the MCP stdio handshake over pipes, and prints exactly one
# ``SANDBOX_RESULT:<json>`` line. Anything else on stdout is server chatter
# and is ignored; host paths never exist in here.
_RUNNER_SOURCE = r"""
import json, os, subprocess, sys, tempfile

def _fail(message):
    sys.stdout.write("SANDBOX_RESULT:" + json.dumps({"ok": False, "error": message}) + "\n")
    sys.stdout.flush()
    raise SystemExit(0)

try:
    bundle = json.loads(sys.stdin.read(2000000))
except Exception:
    _fail("invalid sandbox bundle")
if not isinstance(bundle, dict):
    _fail("invalid sandbox bundle")

try:
    tmpd = tempfile.mkdtemp(prefix="mcp-member-")
    name = str(bundle.get("script_name") or "server.py")
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("bad script name")
    path = os.path.join(tmpd, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(bundle.get("script") or ""))
    proc = subprocess.Popen(
        [str(bundle.get("interpreter") or "python3"), path]
        + [str(a) for a in (bundle.get("extra_argv") or [])],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=tmpd,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
    )
except Exception as exc:
    _fail("sandbox stage failed: " + type(exc).__name__)

def _send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()

def _read_response(want_id):
    while True:
        line = proc.stdout.readline()
        if not line:
            try:
                _, err = proc.communicate(timeout=5)
            except Exception:
                err = ""
            raise RuntimeError("server closed pipe: " + str(err)[-300:])
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("id") == want_id:
            return obj

try:
    _send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                      "clientInfo": {"name": "tomo-member-sandbox", "version": "1"}}})
    init = _read_response(1)
    if "error" in init and init["error"] is not None:
        raise RuntimeError("initialize refused")
    _send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    _send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
           "params": {"name": str(bundle.get("tool") or ""),
                      "arguments": bundle.get("arguments") or {}}})
    result = _read_response(2)
    if "error" in result and result["error"] is not None:
        # Tool-level failure: keep MCP semantics (an error *result*, not a
        # transport failure) so the caller sees the tool's own refusal.
        detail = result["error"]
        if isinstance(detail, dict):
            detail = detail.get("message", "tool error")
        payload = {"content": [{"type": "text", "text": "Error: " + str(detail)[:300]}],
                   "isError": True}
    else:
        payload = result.get("result")
    sys.stdout.write("SANDBOX_RESULT:" + json.dumps({"ok": True, "result": payload}) + "\n")
    sys.stdout.flush()
except Exception as exc:
    _fail(str(exc)[:500])
finally:
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
"""


def sandboxable_stdio_server(server: dict[str, Any]) -> tuple[bool, str]:
    """Whether ``server`` can run in a Member container (fail-closed reason)."""
    command = str(server.get("command") or "").strip()
    base = command.rsplit("/", 1)[-1]
    if base not in MEMBER_INTERPRETERS:
        return False, (
            f"Member stdio execution supports only {sorted(MEMBER_INTERPRETERS)} "
            f"entrypoints, not {base!r}"
        )
    return True, ""


def _read_entrypoint_script(server: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Read (interpreter, script source, extra argv) staged by content.

    The first ``args`` entry naming an existing regular file (bounded) is the
    entrypoint; everything after it is passed through as server argv inside
    the container. Entries before it must be interpreter flags only.
    """
    import os

    args = [str(a) for a in (server.get("args") or [])]
    if len(args) > _MAX_ARGV_ITEMS or sum(len(a) for a in args) > _MAX_ARGV_CHARS:
        raise AccessDenied("MCP server arguments are outside the Member boundary")
    script_index = None
    for index, entry in enumerate(args):
        if os.path.isfile(entry):
            script_index = index
            break
        if entry.startswith("-"):
            continue
        if "/" in entry or "\\" in entry:
            raise AccessDenied("MCP server entrypoint is outside the Member boundary")
    if script_index is None:
        raise AccessDenied("MCP server has no sandbox-staged entrypoint file")
    path = args[script_index]
    try:
        size = os.path.getsize(path)
    except OSError:
        raise AccessDenied("MCP server entrypoint is unavailable") from None
    if size <= 0 or size > _MAX_SCRIPT_BYTES:
        raise AccessDenied("MCP server entrypoint is outside the Member boundary")
    try:
        with open(path, "rb") as handle:
            raw = handle.read(_MAX_SCRIPT_BYTES + 1)
    except OSError:
        raise AccessDenied("MCP server entrypoint is unavailable") from None
    if len(raw) > _MAX_SCRIPT_BYTES:
        raise AccessDenied("MCP server entrypoint is outside the Member boundary")
    try:
        script = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AccessDenied("MCP server entrypoint is outside the Member boundary") from None
    if "\x00" in script:
        raise AccessDenied("MCP server entrypoint is outside the Member boundary")
    command = str(server.get("command") or "").strip()
    interpreter = command.rsplit("/", 1)[-1]
    return interpreter, script, args[script_index + 1:]


def _render_tool_payload(payload: Any) -> str:
    """Render an MCP ``tools/call`` result payload to model-facing text."""
    if payload is None:
        return "(no output)"
    if isinstance(payload, str):
        return payload[:100_000] or "(no output)"
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif "text" in block and isinstance(block["text"], str):
                    parts.append(block["text"])
            text = "\n".join(parts).strip()
            return text[:100_000] if text else "(no output)"
        text = json.dumps(payload, ensure_ascii=False)
        return text[:100_000] if text else "(no output)"
    text = json.dumps(payload, ensure_ascii=False)
    return text[:100_000] if text else "(no output)"


def sandboxed_stdio_call(
    context,
    server: dict[str, Any],
    item: dict[str, Any],
    arguments: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Run one Member MCP stdio tool call in the caller's per-chat container."""
    from app.runtime.isolation.backend import backend as container_backend

    ok, reason = sandboxable_stdio_server(server)
    if not ok:
        raise AccessUnavailable(reason)
    interpreter, script, extra_argv = _read_entrypoint_script(server)
    bundle = {
        "interpreter": interpreter,
        "script": script,
        "script_name": "server.py" if "python" in interpreter else "server.js",
        "extra_argv": extra_argv,
        "tool": str(item.get("name") or ""),
        "arguments": dict(arguments or {}),
    }
    try:
        result = container_backend.execute(
            context,
            ["python", "-c", _RUNNER_SOURCE],
            stdin=json.dumps(bundle),
            timeout=float(timeout),
        )
    except (AccessDenied, AccessUnavailable):
        raise
    except Exception as exc:
        raise AccessUnavailable("Member tool backend is unavailable") from exc
    marker = "SANDBOX_RESULT:"
    envelope = None
    for line in (result.stdout or "").splitlines():
        if line.startswith(marker):
            try:
                envelope = json.loads(line[len(marker):])
            except (ValueError, TypeError):
                envelope = None
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        # Generic model-facing error: container stderr may echo staged
        # filenames or interpreter internals, never host paths or secrets
        # (the server env is never forwarded into the sandbox).
        raise AccessUnavailable("Member tool execution failed")
    return _render_tool_payload(envelope.get("result"))


def check_member_http_service(server: dict[str, Any]) -> None:
    """Fail closed unless ``server`` may serve a Member HTTP tool call."""
    from app.runtime.net_policy import require_egress
    from app.runtime.tools.web_fetch import _check_url

    require_egress("mcp")
    url = str(server.get("url") or "").strip()
    blocked = _check_url(url)
    if blocked is not None:
        raise AccessDenied(blocked)


async def member_http_call(
    context,
    server: dict[str, Any],
    item: dict[str, Any],
    arguments: dict[str, Any],
    *,
    session_factory=None,
) -> str:
    """One-shot Member Streamable-HTTP tool call with sanitized credentials.

    Server-wide ``headers``/``env`` are never inherited: the request carries
    no stored credential. Egress admission and the SSRF guard run first.
    Unlike Admin calls this never populates the persistent ``_live``
    sessions — each call opens exactly one short-lived session and closes it.
    """
    from contextlib import AsyncExitStack

    from app.runtime.mcp.results import render_tool_result

    check_member_http_service(server)
    clean = dict(server)
    clean["headers"] = {}
    clean["env"] = {}
    if session_factory is not None:
        stack = AsyncExitStack()
        try:
            owned = await session_factory(clean)
            live_stack, session, _init = owned[0], owned[1], owned[2:]
            stack = live_stack
        except Exception as exc:
            await stack.aclose()
            raise AccessUnavailable("Member tool backend is unavailable") from exc
    else:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        import httpx

        stack = AsyncExitStack()
        try:
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(headers={}, follow_redirects=True)
            )
            read, write, _get_session_id = await stack.enter_async_context(
                streamable_http_client(clean["url"], http_client=http_client)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            raise AccessUnavailable("Member tool backend is unavailable") from exc
    try:
        result = await session.call_tool(str(item.get("name") or ""), dict(arguments or {}))
    except Exception as exc:
        raise AccessUnavailable("Member tool execution failed") from exc
    finally:
        await stack.aclose()
    return render_tool_result(result)


__all__ = [
    "MEMBER_INTERPRETERS",
    "sandboxable_stdio_server",
    "sandboxed_stdio_call",
    "check_member_http_service",
    "member_http_call",
]
