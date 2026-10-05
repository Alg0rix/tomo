"""Member plugin-tool invocation inside the caller's per-chat container.

Plugin code normally executes in the coordinator process with server-host
authority (``PluginManager.execute``). Members must never reach that path:
a Member call replays only the plugin's ``plugin.py`` **source text**
(bounded, read server-side, never echoed) inside the caller's own per-chat
restricted container, against a stub SDK surface that captures ``tool``
registrations. Pages, routers, home cards, background hooks and all host
imports beyond the standard library are unavailable in there — only simple
supported (pure-computation) plugin tools run sandboxed; anything else
fails closed with an explicit boundary error instead of running on the host.

Like MCP Member calls, this rides on :meth:`ContainerBackend.execute`, so
owner-scoped mounts (kernel RO), quota-ledger admission,
duration/process-tree supervision, generation-gated revocation and
confirmed teardown all apply. Plugin install/enable/disable/reload stay
Admin-only management operations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.runtime.access import AccessDenied, AccessUnavailable

_MAX_PLUGIN_BYTES = 256 * 1024

# In-container runner (stdlib only). Bundle on stdin:
# {plugin_id, source, tool, arguments}. Builds a stub ``api`` capturing
# tool registrations, runs ``setup(api)``, invokes the matching handler,
# and prints exactly one ``SANDBOX_RESULT:<json>`` line.
_RUNNER_SOURCE = r"""
import json, sys

def _fail(message):
    sys.stdout.write("SANDBOX_RESULT:" + json.dumps({"ok": False, "error": message}) + "\n")
    sys.stdout.flush()
    raise SystemExit(0)

try:
    bundle = json.loads(sys.stdin.read(1000000))
except Exception:
    _fail("invalid sandbox bundle")

class _Noop:
    def __call__(self, *args, **kwargs):
        return None
    def __getattr__(self, name):
        return _Noop()

class _Api:
    def __init__(self):
        self.tools = {}
    def tool(self, name, description, parameters, handler):
        import re
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(name or "")):
            raise ValueError("bad tool name")
        if not callable(handler):
            raise ValueError("bad tool handler")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError("bad tool schema")
        self.tools[str(name)] = handler
    def __getattr__(self, name):
        return _Noop()

try:
    plugin_id = str(bundle.get("plugin_id") or "")
    source = str(bundle.get("source") or "")
    namespace = {"__name__": "sandbox_plugin"}
    exec(compile(source, plugin_id + "/plugin.py", "exec"), namespace)
    setup = namespace.get("setup")
    if not callable(setup):
        raise ValueError("plugin has no setup(api)")
    api = _Api()
    setup(api)
    short = str(bundle.get("tool") or "")
    handler = api.tools.get(short)
    if handler is None:
        raise ValueError("tool is not registered by this plugin")
    arguments = bundle.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    outcome = handler(dict(arguments))
    if not isinstance(outcome, str):
        outcome = json.dumps(outcome)
    sys.stdout.write("SANDBOX_RESULT:" + json.dumps({"ok": True, "result": outcome}) + "\n")
    sys.stdout.flush()
except Exception as exc:
    _fail(type(exc).__name__ + ": " + str(exc)[:300])
"""


def read_plugin_source(plugin_id: str, path: Path) -> str:
    """Read a plugin's ``plugin.py`` source for sandbox staging (bounded)."""
    candidate = Path(path) / "plugin.py"
    try:
        size = candidate.stat().st_size
    except OSError:
        raise AccessDenied("Plugin tool is unavailable") from None
    if size <= 0 or size > _MAX_PLUGIN_BYTES:
        raise AccessDenied("Plugin tool is outside the Member boundary")
    try:
        raw = candidate.read_bytes()
    except OSError:
        raise AccessDenied("Plugin tool is unavailable") from None
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AccessDenied("Plugin tool is outside the Member boundary") from None
    if "\x00" in source:
        raise AccessDenied("Plugin tool is outside the Member boundary")
    return source


def sandboxed_plugin_call(
    context,
    plugin_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> str:
    """Run one Member plugin-tool call in the caller's per-chat container."""
    from app.plugins.manager import get_manager
    from app.runtime.isolation.backend import backend as container_backend

    manager = get_manager()
    with manager._lock:
        row = manager._rows.get(plugin_id)
        active = manager._active.get(plugin_id)
    if row is None or not row.get("enabled") or active is None:
        raise AccessDenied("External tool is unavailable")
    registered = active["api"].tools.get(tool_name)
    if registered is None:
        raise AccessDenied("External tool is unavailable")
    source = read_plugin_source(plugin_id, Path(row["path"]))
    prefix = f"plugin__{plugin_id}__"
    if not tool_name.startswith(prefix) or len(tool_name) == len(prefix):
        raise AccessDenied("External tool is unavailable")
    short = tool_name[len(prefix):]
    bundle = {
        "plugin_id": plugin_id,
        "source": source,
        "tool": short,
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
        raise AccessUnavailable("Member tool execution failed")
    outcome = envelope.get("result")
    return outcome if isinstance(outcome, str) else json.dumps(outcome)


__all__ = ["read_plugin_source", "sandboxed_plugin_call"]
