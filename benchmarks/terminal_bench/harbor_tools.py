"""Harbor ↔ Tomo tool bridge: coding tools run inside the task container.

Tomo's tool backends are sync; Harbor's ``environment.exec`` is async. Calls
from ``run_turn`` are bridged via a short-lived thread + ``asyncio.run``.
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import shlex
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from harbor.environments.base import BaseEnvironment, ExecResult

_environment: ContextVar[BaseEnvironment | None] = ContextVar(
    "tomo_harbor_environment", default=None
)
_commands: ContextVar[int] = ContextVar("tomo_harbor_commands", default=0)

_CODING_TOOLS = frozenset(
    {
        "bash",
        "runpy",
        "read_file",
        "write_file",
        "str_replace",
        "patch",
        "list_dir",
        "search_files",
        "delete_file",
    }
)


def _run_coro(coro):
    """Run an async Harbor call from a sync tool backend."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    def _in_thread():
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_in_thread).result()


def _exec(command: str, *, timeout_sec: int | None = 120) -> ExecResult:
    env = _environment.get()
    if env is None:
        raise RuntimeError("Harbor environment not bound")
    _commands.set(_commands.get() + 1)
    return _run_coro(env.exec(command=command, timeout_sec=timeout_sec))


def _format_exec(result: ExecResult) -> str:
    parts: list[str] = []
    stdout = (result.stdout or "").rstrip("\n")
    stderr = (result.stderr or "").rstrip("\n")
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    if result.return_code not in (0, None):
        parts.append(f"exit code: {result.return_code}")
    if not parts:
        return "(no output)"
    return "\n".join(parts)


def _clip(text: str, limit: int = 100_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, {len(text)} chars total]"


def harbor_bash(arguments: dict[str, Any]) -> str:
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        return "Error: 'command' argument must be a non-empty string"
    if arguments.get("background"):
        return "Error: background bash is not supported in Harbor Terminal-Bench mode"
    timeout = arguments.get("timeout")
    try:
        timeout_sec = int(float(timeout)) if timeout is not None else 120
    except (TypeError, ValueError):
        timeout_sec = 120
    timeout_sec = max(1, min(timeout_sec, 600))
    try:
        result = _exec(f"bash -lc {shlex.quote(command)}", timeout_sec=timeout_sec)
    except Exception as exc:
        return f"Error: could not run command: {exc}"
    return _clip(_format_exec(result))


def harbor_runpy(arguments: dict[str, Any]) -> str:
    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        return "Error: 'code' argument must be a non-empty string"
    payload = base64.b64encode(code.encode("utf-8")).decode("ascii")
    cmd = (
        "python3 -c "
        + shlex.quote(
            "import base64,sys; exec(base64.b64decode(sys.argv[1]).decode())"
        )
        + " "
        + shlex.quote(payload)
    )
    try:
        result = _exec(cmd, timeout_sec=120)
    except Exception as exc:
        return f"Error: could not run python: {exc}"
    return _clip(_format_exec(result))


def harbor_read_file(arguments: dict[str, Any]) -> str:
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        return "Error: 'path' argument must be a non-empty string"
    offset = int(arguments.get("offset") or 1)
    limit = int(arguments.get("limit") or 500)
    offset = max(1, offset)
    limit = max(1, min(limit, 2000))
    # 1-based line numbers matching Tomo's read_file shape (N|content).
    py = f"""
import pathlib
p = pathlib.Path({path!r})
if not p.is_file():
    print(f"Error: file not found: {{p}}")
    raise SystemExit(0)
lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
start = {offset} - 1
end = start + {limit}
chunk = lines[start:end]
for i, line in enumerate(chunk, start={offset}):
    print(f"{{i}}|{{line}}")
if end < len(lines):
    print(f"... truncated; next offset={{end + 1}} ({{len(lines)}} lines total)")
"""
    payload = base64.b64encode(py.encode("utf-8")).decode("ascii")
    cmd = (
        "python3 -c "
        + shlex.quote("import base64,sys; exec(base64.b64decode(sys.argv[1]).decode())")
        + " "
        + shlex.quote(payload)
    )
    try:
        result = _exec(cmd, timeout_sec=60)
    except Exception as exc:
        return f"Error: could not read file: {exc}"
    return _clip(_format_exec(result))


def harbor_write_file(arguments: dict[str, Any]) -> str:
    path = arguments.get("path")
    content = arguments.get("content")
    if not isinstance(path, str) or not path.strip():
        return "Error: 'path' argument must be a non-empty string"
    if not isinstance(content, str):
        return "Error: 'content' argument must be a string"
    mode = (arguments.get("mode") or "overwrite").strip().lower()
    if arguments.get("overwrite") is False:
        mode = "create"
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    py = f"""
import base64, pathlib
p = pathlib.Path({path!r})
mode = {mode!r}
data = base64.b64decode({b64!r})
if mode == 'create' and p.exists():
    print(f"Error: file already exists: {{p}}")
    raise SystemExit(0)
p.parent.mkdir(parents=True, exist_ok=True)
if mode == 'append':
    with p.open('ab') as f:
        f.write(data)
else:
    p.write_bytes(data)
print(f"Wrote {{len(data)}} bytes to {{p}} (mode={{mode}})")
"""
    payload = base64.b64encode(py.encode("utf-8")).decode("ascii")
    cmd = (
        "python3 -c "
        + shlex.quote("import base64,sys; exec(base64.b64decode(sys.argv[1]).decode())")
        + " "
        + shlex.quote(payload)
    )
    try:
        result = _exec(cmd, timeout_sec=60)
    except Exception as exc:
        return f"Error: could not write file: {exc}"
    return _clip(_format_exec(result))


def harbor_str_replace(arguments: dict[str, Any]) -> str:
    path = arguments.get("path")
    old = arguments.get("old_string")
    new = arguments.get("new_string")
    if not isinstance(path, str) or not path.strip():
        return "Error: 'path' argument must be a non-empty string"
    if not isinstance(old, str) or not isinstance(new, str):
        return "Error: old_string and new_string must be strings"
    count = arguments.get("count", 1)
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    payload_obj = {"path": path, "old": old, "new": new, "count": count}
    blob = base64.b64encode(json.dumps(payload_obj).encode("utf-8")).decode("ascii")
    py = f"""
import base64, json, pathlib
spec = json.loads(base64.b64decode({blob!r}).decode())
p = pathlib.Path(spec['path'])
if not p.is_file():
    print(f"Error: file not found: {{p}}")
    raise SystemExit(0)
text = p.read_text(encoding='utf-8')
old, new, count = spec['old'], spec['new'], spec['count']
n = text.count(old)
if count == -1:
    if n == 0:
        print('Error: old_string not found')
        raise SystemExit(0)
    p.write_text(text.replace(old, new), encoding='utf-8')
    print(f'Replaced {{n}} occurrence(s) in {{p}}')
else:
    if n != count:
        print(f'Error: expected {{count}} occurrence(s), found {{n}}')
        raise SystemExit(0)
    p.write_text(text.replace(old, new, count), encoding='utf-8')
    print(f'Replaced {{count}} occurrence(s) in {{p}}')
"""
    payload = base64.b64encode(py.encode("utf-8")).decode("ascii")
    cmd = (
        "python3 -c "
        + shlex.quote("import base64,sys; exec(base64.b64decode(sys.argv[1]).decode())")
        + " "
        + shlex.quote(payload)
    )
    try:
        result = _exec(cmd, timeout_sec=60)
    except Exception as exc:
        return f"Error: could not str_replace: {exc}"
    return _clip(_format_exec(result))


def harbor_list_dir(arguments: dict[str, Any]) -> str:
    path = arguments.get("path") or "."
    if not isinstance(path, str):
        return "Error: 'path' must be a string"
    cmd = f"ls -la {shlex.quote(path)}"
    try:
        result = _exec(cmd, timeout_sec=30)
    except Exception as exc:
        return f"Error: could not list dir: {exc}"
    return _clip(_format_exec(result))


def harbor_search_files(arguments: dict[str, Any]) -> str:
    query = arguments.get("query") or arguments.get("pattern") or ""
    path = arguments.get("path") or "."
    if not isinstance(query, str) or not query.strip():
        return "Error: 'query' argument must be a non-empty string"
    cmd = f"grep -RIn --exclude-dir=.git {shlex.quote(query)} {shlex.quote(path)} | head -n 200"
    try:
        result = _exec(cmd, timeout_sec=60)
    except Exception as exc:
        return f"Error: could not search: {exc}"
    return _clip(_format_exec(result))


def harbor_delete_file(arguments: dict[str, Any]) -> str:
    path = arguments.get("path")
    if not isinstance(path, str) or not path.strip():
        return "Error: 'path' argument must be a non-empty string"
    cmd = f"rm -f {shlex.quote(path)} && echo deleted {shlex.quote(path)}"
    try:
        result = _exec(cmd, timeout_sec=30)
    except Exception as exc:
        return f"Error: could not delete: {exc}"
    return _clip(_format_exec(result))


def harbor_patch(arguments: dict[str, Any]) -> str:
    """Apply a unified diff via ``patch`` inside the container."""
    diff = arguments.get("diff") or arguments.get("patch") or ""
    if not isinstance(diff, str) or not diff.strip():
        return "Error: 'diff' argument must be a non-empty string"
    b64 = base64.b64encode(diff.encode("utf-8")).decode("ascii")
    cmd = (
        f"echo {shlex.quote(b64)} | base64 -d | patch -p0"
    )
    try:
        result = _exec(cmd, timeout_sec=60)
    except Exception as exc:
        return f"Error: could not patch: {exc}"
    return _clip(_format_exec(result))


_HARBOR_BACKENDS: dict[str, Any] = {
    "bash": harbor_bash,
    "runpy": harbor_runpy,
    "read_file": harbor_read_file,
    "write_file": harbor_write_file,
    "str_replace": harbor_str_replace,
    "patch": harbor_patch,
    "list_dir": harbor_list_dir,
    "search_files": harbor_search_files,
    "delete_file": harbor_delete_file,
}


@contextmanager
def bind_harbor_tools(environment: BaseEnvironment) -> Iterator[None]:
    """Temporarily route coding tools through Harbor ``environment.exec``."""
    from app.runtime.tools import registry as tool_registry

    token_env = _environment.set(environment)
    token_cmds = _commands.set(0)
    originals = {
        name: tool_registry._BACKENDS[name]
        for name in _CODING_TOOLS
        if name in tool_registry._BACKENDS
    }
    try:
        for name, runner in _HARBOR_BACKENDS.items():
            tool_registry._BACKENDS[name] = runner
        yield
    finally:
        for name, runner in originals.items():
            tool_registry._BACKENDS[name] = runner
        _environment.reset(token_env)
        _commands.reset(token_cmds)


def commands_executed() -> int:
    return _commands.get()


# Tools exposed to the LLM for Terminal-Bench (coder-like, no delegate/swarm).
TB_TOOL_NAMES = frozenset(
    {
        "bash",
        "runpy",
        "read_file",
        "write_file",
        "str_replace",
        "patch",
        "list_dir",
        "search_files",
        "delete_file",
        "todo",
    }
)
