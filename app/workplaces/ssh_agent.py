"""Supervised SSH destination agent (stdlib only).

Runs on the SSH destination (via ``python3`` over the SSH transport, or as a
local subprocess in tests) and enforces the same destination-owned execution
contract as the tunnel connector:

* every execution RPC carries an immutable ``exec_context`` envelope
  (owner, session, mode, destination, access generation, resources, quota);
* ``admit`` registers (owner, session) at a generation and attests the
  operator-provisioned sandbox marker; all other RPCs require a live
  admission with a matching generation (revocation bumps fail closed);
* file/exec paths resolve under destination-owned
  ``<base>/<workplace_id>/`` scopes — coordinator paths are never trusted;
  read-only scopes reject writes; nothing escapes its root;
* per-call deadlines come from the envelope quota slice;
* ``teardown`` kills admitted processes and drops the admission with an ack.

Operator provisioning (destination side): create ``<base>/`` and write the
pinned image marker to ``<base>/.tomo-sandbox-image`` out-of-band, then
record the same marker plus ``<base>`` on the workplace row
(``ssh_sandbox_root``/``ssh_sandbox_image``). Without the marker the agent
still runs but attests ``sandbox: false`` and the coordinator refuses
restricted work. This file must never receive credentials: stdin is one
JSON request per line, stdout one JSON response per line.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

CONTRACT = 2
_SCOPE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_OUTPUT = 64 * 1024

_lock = threading.Lock()
_admitted: dict[tuple[str, str], dict] = {}
_jobs: dict[str, subprocess.Popen] = {}


def _respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _refuse(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _base() -> Path:
    root = os.environ.get("TOMO_SSH_AGENT_ROOT", "").strip()
    if not root:
        return Path(".")
    return Path(root)


def _marker() -> str:
    try:
        text = (_base() / ".tomo-sandbox-image").read_text(encoding="utf-8")
    except OSError:
        return ""
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def _envelope(params: dict) -> dict | str:
    env = (params or {}).get("exec_context")
    if not isinstance(env, dict) or env.get("v") != 1:
        return "exec_context is required"
    for key in ("owner_user_id", "session_id", "destination_id"):
        if not str(env.get(key) or "").strip():
            return f"exec_context missing {key}"
    if env.get("execution_mode") not in ("restricted", "unrestricted"):
        return "exec_context has unknown execution mode"
    try:
        gen = int(env.get("access_generation", 0))
    except (TypeError, ValueError):
        return "exec_context has invalid generation"
    if gen < 0:
        return "exec_context has invalid generation"
    return env


def _roots(env: dict) -> dict[str, dict] | str:
    roots: dict[str, dict] = {}
    resources = env.get("resources") or []
    if not isinstance(resources, list):
        return "exec_context resources must be a list"
    for item in resources:
        if not isinstance(item, dict) or item.get("transfer_only"):
            continue
        scope = str(item.get("workplace_id") or "")
        if not _SCOPE_RE.fullmatch(scope):
            continue
        path = _base() / scope
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return f"cannot prepare scope {scope}: {exc}"
        roots[scope] = {"path": path, "writable": item.get("permission") == "read_write"}
    return roots


def _own_workplace() -> str:
    return os.environ.get("TOMO_SSH_AGENT_WORKPLACE", "").strip()


def _admission(method: str, params: dict):
    if method == "admit":
        env = _envelope(params)
        if isinstance(env, str):
            return None, None, env
        own = _own_workplace()
        if own and str(env["destination_id"]) != own:
            return None, None, "exec_context destination does not match this destination"
        roots = _roots(env)
        if isinstance(roots, str):
            return None, None, roots
        return env, roots, None
    env = _envelope(params)
    if isinstance(env, str):
        return None, None, env
    roots = _roots(env)
    if isinstance(roots, str):
        return None, None, roots
    key = (str(env["owner_user_id"]), str(env["session_id"]))
    with _lock:
        rec = _admitted.get(key)
    if rec is None:
        return None, None, "no live execution admission for this session"
    if rec["destination"] != env["destination_id"] or rec["generation"] != int(env["access_generation"]):
        return None, None, "stale execution generation: access changed; re-admit"
    return env, roots, None


def _resolve(roots: dict, env: dict, path: str, *, write: bool):
    text = (path or "").strip().replace("\x00", "")
    if not text:
        return None, "path must not be empty"
    active = str(env.get("active_workplace_id") or "")
    ordered = [active] + [s for s in roots if s != active]
    candidate: dict | None = None
    rel: str | None = None
    if os.path.isabs(text):
        target = Path(text)
        for scope in ordered:
            root = roots.get(scope)
            if root is None:
                continue
            try:
                target.resolve().relative_to(root["path"].resolve())
                candidate, rel = root, "."
                break
            except (ValueError, OSError):
                continue
        if candidate is None:
            return None, "path is outside destination scopes"
    else:
        scope = active if active in roots else (ordered[0] if ordered else None)
        if scope is None or scope not in roots:
            return None, "no writable destination scope"
        candidate, rel = roots[scope], text
    if write and not candidate["writable"]:
        return None, "destination scope is read-only"
    base = candidate["path"]
    try:
        resolved = (base / rel).resolve() if rel != "." else base.resolve()
        resolved.relative_to(base.resolve())
    except (ValueError, OSError):
        return None, "path escapes the destination scope"
    # Symlink containment: a link pointing outside the scope is rejected.
    if resolved.is_symlink():
        try:
            resolved.resolve().relative_to(base.resolve())
        except (ValueError, OSError):
            return None, "path escapes the destination scope"
    return resolved, None


def _clip(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + "\n[truncated]"


def op_admit(params: dict) -> dict:
    env, roots, err = _admission("admit", params)
    if err:
        return _refuse(err)
    assert isinstance(env, dict)
    key = (str(env["owner_user_id"]), str(env["session_id"]))
    with _lock:
        _admitted[key] = {
            "destination": str(env["destination_id"]),
            "generation": int(env["access_generation"]),
        }
    return {
        "ok": True,
        "result": {
            "contract": CONTRACT,
            "sandbox": bool(_marker()),
            "image": _marker(),
            "scopes": sorted(roots.keys()) if isinstance(roots, dict) else [],
        },
    }


def op_exec(params: dict) -> dict:
    env, roots, err = _admission("exec", params)
    if err:
        return _refuse(err)
    assert isinstance(env, dict) and isinstance(roots, dict)
    script = params.get("script", params.get("command", ""))
    if not isinstance(script, str) or not script.strip():
        return _refuse("script must be a non-empty string")
    try:
        want = float(params.get("timeout", 60) or 60)
    except (TypeError, ValueError):
        want = 60.0
    try:
        cap = float((env.get("quota") or {}).get("duration_seconds", 0) or 0)
    except (TypeError, ValueError):
        cap = 0.0
    deadline = min(max(want, 1.0), cap) if cap > 0 else min(max(want, 1.0), 600.0)
    cwd_param = str(params.get("cwd") or "").strip()
    if cwd_param:
        cwd, err = _resolve(roots, env, cwd_param, write=False)
        if err:
            return _refuse(err)
    else:
        active = str(env.get("active_workplace_id") or "")
        cwd = roots[active]["path"] if active in roots else None
        if cwd is None:
            return _refuse("no destination scope for execution")
    try:
        proc = subprocess.Popen(
            ["bash", "-c", script],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return _refuse(f"could not start command: {exc}")
    try:
        stdout, stderr = proc.communicate(timeout=deadline)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        code = 124
        stderr = (stderr or "") + f"\n[duration bound reached: {deadline:g}s]"
    return {
        "ok": True,
        "result": {
            "stdout": _clip(stdout or ""),
            "stderr": _clip(stderr or ""),
            "exit_code": int(code or 0),
        },
    }


def op_read(params: dict, *, raw: bool = False) -> dict:
    env, roots, err = _admission("exec", params)
    if err:
        return _refuse(err)
    assert isinstance(env, dict) and isinstance(roots, dict)
    target, err = _resolve(roots, env, str(params.get("path") or ""), write=False)
    if err:
        return _refuse(err)
    assert target is not None
    try:
        if not target.is_file():
            return _refuse("not a file")
        if target.stat().st_size > 4 * 1024 * 1024:
            return _refuse("file too large")
        data = target.read_bytes()
    except OSError as exc:
        return _refuse(f"could not read file: {exc}")
    if raw:
        return {"ok": True, "result": {"content_b64": base64.b64encode(data).decode("ascii")}}
    try:
        return {"ok": True, "result": {"content": data.decode("utf-8")}}
    except UnicodeDecodeError:
        return _refuse("not a text file")


def op_write(params: dict, *, raw: bool = False) -> dict:
    env, roots, err = _admission("exec", params)
    if err:
        return _refuse(err)
    assert isinstance(env, dict) and isinstance(roots, dict)
    target, err = _resolve(roots, env, str(params.get("path") or ""), write=True)
    if err:
        return _refuse(err)
    assert target is not None
    if raw:
        try:
            data = base64.b64decode(params.get("content_b64") or "")
        except (ValueError, TypeError):
            return _refuse("invalid base64 content")
    else:
        content = params.get("content")
        if not isinstance(content, str):
            return _refuse("content must be a string")
        data = content.encode("utf-8")
    if len(data) > 4 * 1024 * 1024:
        return _refuse("content too large")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        return _refuse(f"could not write file: {exc}")
    return {"ok": True, "result": {"ok": True, "path": str(params.get("path") or "")}}


def op_teardown(params: dict) -> dict:
    env = _envelope(params)
    if isinstance(env, str):
        return _refuse(env)
    key = (str(env["owner_user_id"]), str(env["session_id"]))
    with _lock:
        rec = _admitted.pop(key, None)
    # Generation-tolerant: post-revoke teardown still acknowledges so the
    # coordinator's revocation barrier can confirm.
    _ = rec
    return {"ok": True, "result": {"torn_down": True}}


_OPS = {
    "admit": op_admit,
    "exec_admit": op_admit,
    "exec": op_exec,
    "exec_bash": op_exec,
    "bash": op_exec,
    "read": op_read,
    "read_file": op_read,
    "teardown": op_teardown,
    "exec_teardown": op_teardown,
}


def handle(request: dict) -> dict:
    op = str((request or {}).get("op") or "").strip()
    params = (request or {}).get("params")
    if not isinstance(params, dict):
        params = {}
    if op in ("read_b64", "read_file_b64"):
        return op_read(params, raw=True)
    if op in ("write_b64", "write_file_b64"):
        return op_write(params, raw=True)
    if op in ("write", "write_file"):
        return op_write(params)
    handler = _OPS.get(op)
    if handler is None:
        return _refuse(f"unknown op: {op}")
    try:
        return handler(params)
    except Exception as exc:  # agent must always answer JSON, never traceback
        return _refuse(f"agent error: {type(exc).__name__}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _respond(_refuse("invalid JSON request"))
            continue
        if not isinstance(request, dict):
            _respond(_refuse("request must be an object"))
            continue
        _respond(handle(request))


if __name__ == "__main__":
    main()
