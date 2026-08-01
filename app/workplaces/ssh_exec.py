"""SSH workplace execution via Paramiko (agent tools).

Mirrors connector RPC shapes as Python return values that
:mod:`app.runtime.tools.workplace_remote` formats for the agent loop.
"""

from __future__ import annotations

import base64
import io
import shlex
import time
from pathlib import Path
from typing import Any

import paramiko

_DEFAULT_TIMEOUT = 60.0
_MAX_TIMEOUT = 600.0
_MAX_OUTPUT = 64 * 1024


def _clip(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + "\n[truncated]"


def _timeout(raw: Any, default: float = _DEFAULT_TIMEOUT) -> float:
    try:
        v = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        v = default
    if v <= 0:
        v = default
    return min(v, _MAX_TIMEOUT)


def connect(workplace: dict[str, Any]) -> paramiko.SSHClient:
    """Open an SSH client from a secrets workplace dict."""
    host = (workplace.get("ssh_host") or "").strip()
    port = int(workplace.get("ssh_port") or 22)
    user = (workplace.get("ssh_user") or "").strip()
    password = workplace.get("ssh_password") or ""
    key = workplace.get("ssh_key") or ""
    if not host or not user:
        raise ValueError("SSH workplace needs ssh_host and ssh_user")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known = Path.home() / ".ssh" / "known_hosts"
    if known.is_file():
        try:
            client.load_host_keys(str(known))
        except OSError:
            pass
    # Require a known host key (add via ssh-keyscan / known_hosts). Never AutoAdd.
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    pkey = None
    if key.strip():
        for loader in (
            paramiko.RSAKey,
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
        ):
            try:
                pkey = loader.from_private_key(io.StringIO(key))
                break
            except Exception:
                continue
        if pkey is None:
            try:
                pkey = paramiko.DSSKey.from_private_key(io.StringIO(key))
            except Exception as exc:
                raise ValueError(f"Could not parse SSH private key: {exc}") from exc
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password or None,
        pkey=pkey,
        timeout=15,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def _remote_root(workplace: dict[str, Any]) -> str:
    root = (workplace.get("root_path") or "").strip()
    return root or "."


def _remote_path(workplace: dict[str, Any], path: str) -> str:
    path = (path or "").strip()
    if not path:
        raise ValueError("path must not be empty")
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return path
    root = _remote_root(workplace).rstrip("/")
    if root in ("", "."):
        return path
    return f"{root}/{path.lstrip('/')}"


def exec_bash(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    script = (params.get("script") or params.get("command") or "").strip()
    if not script:
        raise ValueError("'script' is required")
    timeout = _timeout(params.get("timeout"))
    cwd = (params.get("cwd") or "").strip() or _remote_root(workplace)
    t0 = time.time()
    client = connect(workplace)
    try:
        # Run under bash -lc in the workplace root when relative.
        wrapped = f"cd {shlex.quote(cwd)} && bash -s"
        _stdin, stdout, stderr = client.exec_command(wrapped, timeout=timeout)
        _stdin.write(script)
        _stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
    finally:
        client.close()
    return {
        "stdout": _clip(out),
        "stderr": _clip(err),
        "exit_code": code,
        "execution_time": time.time() - t0,
    }


def exec_python(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    code = (params.get("code") or "").strip()
    if not code:
        raise ValueError("'code' is required")
    timeout = _timeout(params.get("timeout"))
    cwd = (params.get("cwd") or "").strip() or _remote_root(workplace)
    t0 = time.time()
    client = connect(workplace)
    try:
        wrapped = f"cd {shlex.quote(cwd)} && python3 -"
        _stdin, stdout, stderr = client.exec_command(wrapped, timeout=timeout)
        _stdin.write(code)
        _stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code_rc = stdout.channel.recv_exit_status()
    finally:
        client.close()
    return {
        "stdout": _clip(out),
        "stderr": _clip(err),
        "exit_code": code_rc,
        "execution_time": time.time() - t0,
    }


def read_file(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    path = _remote_path(workplace, str(params.get("path") or ""))
    client = connect(workplace)
    try:
        sftp = client.open_sftp()
        try:
            with sftp.file(path, "r") as f:
                data = f.read()
        finally:
            sftp.close()
    finally:
        client.close()
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
        size = len(data)
    else:
        text = str(data)
        size = len(text)
    return {"content": text, "size": size, "path": path}


def write_file(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    path = _remote_path(workplace, str(params.get("path") or ""))
    content = params.get("content")
    if not isinstance(content, str):
        raise ValueError("'content' must be a string")
    mode = (params.get("mode") or "overwrite").strip().lower()
    if mode not in ("create", "overwrite", "append"):
        raise ValueError("'mode' must be create, overwrite, or append")
    client = connect(workplace)
    try:
        sftp = client.open_sftp()
        try:
            if mode == "create":
                try:
                    sftp.stat(path)
                    raise ValueError(
                        f"file already exists: {params.get('path')}. "
                        "Use mode='overwrite' to replace, or str_replace/patch for edits."
                    )
                except OSError:
                    pass  # missing → ok for create
            # Ensure parent dirs.
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            if parent:
                _mkdir_p(sftp, parent)
            flags = "a" if mode == "append" else "w"
            with sftp.file(path, flags) as f:
                f.write(content)
        finally:
            sftp.close()
    finally:
        client.close()
    return {"ok": True, "path": path, "mode": mode}


def read_file_b64(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Chunked binary read — portal-compatible with the connector RPC."""
    path = _remote_path(workplace, str(params.get("path") or ""))
    offset = int(params.get("offset") or 0)
    size = int(params.get("size") or 0)
    if offset < 0:
        raise ValueError("offset must be >= 0")
    client = connect(workplace)
    try:
        sftp = client.open_sftp()
        try:
            st = sftp.stat(path)
            total = int(getattr(st, "st_size", 0) or 0)
            with sftp.file(path, "rb") as f:
                if offset:
                    f.seek(offset)
                if size > 0:
                    data = f.read(size)
                else:
                    data = f.read()
        finally:
            sftp.close()
    finally:
        client.close()
    if not isinstance(data, (bytes, bytearray)):
        data = bytes(data or b"")
    return {
        "data": base64.b64encode(bytes(data)).decode("ascii"),
        "bytes_read": len(data),
        "total_size": total,
        "path": path,
    }


def write_file_b64(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Chunked binary write with ``.part`` staging (portal-compatible)."""
    path = _remote_path(workplace, str(params.get("path") or ""))
    raw_b64 = params.get("data")
    if not isinstance(raw_b64, str):
        raise ValueError("'data' must be a base64 string")
    try:
        decoded = base64.b64decode(raw_b64)
    except Exception as exc:
        raise ValueError(f"invalid base64: {exc}") from exc
    offset = int(params.get("offset") or 0)
    is_last = True if params.get("is_last") is None else bool(params.get("is_last"))
    if offset < 0:
        raise ValueError("offset must be >= 0")
    part = path + ".part"
    client = connect(workplace)
    try:
        sftp = client.open_sftp()
        try:
            if offset == 0:
                parent = path.rsplit("/", 1)[0] if "/" in path else ""
                if parent:
                    _mkdir_p(sftp, parent)
                with sftp.file(part, "wb") as f:
                    f.write(decoded)
            else:
                with sftp.file(part, "ab") as f:
                    f.write(decoded)
            if is_last:
                try:
                    sftp.remove(path)
                except OSError:
                    pass
                sftp.rename(part, path)
        finally:
            sftp.close()
    finally:
        client.close()
    return {"ok": True, "path": path}


def str_replace(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.tools.text_edit import apply_str_replace

    path = str(params.get("path") or "")
    old = params.get("old_string")
    new = params.get("new_string")
    if not isinstance(old, str) or not old:
        raise ValueError("'old_string' must be a non-empty string")
    if not isinstance(new, str):
        raise ValueError("'new_string' must be a string")
    count = params.get("count", 1)
    try:
        count_i = int(count) if count is not None else 1
    except (TypeError, ValueError) as exc:
        raise ValueError("'count' must be an integer") from exc
    got = read_file(workplace, {"path": path})
    applied = apply_str_replace(got["content"], old, new, count=count_i)
    if isinstance(applied, str):
        raise ValueError(applied.removeprefix("Error: ").strip() or applied)
    updated, n = applied
    write_file(workplace, {"path": path, "content": updated, "mode": "overwrite"})
    return {"ok": True, "path": got["path"], "replacements": n}


def patch_file(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    from app.runtime.tools.text_edit import apply_patch_to_content, is_create_new_file_patch

    path = str(params.get("path") or "")
    patch_text = params.get("patch")
    if not isinstance(patch_text, str) or not patch_text.strip():
        raise ValueError("'patch' must be a non-empty string")
    creating = is_create_new_file_patch(patch_text)
    try:
        got = read_file(workplace, {"path": path})
        raw = got["content"]
        out_path = got["path"]
    except Exception:
        if not creating:
            raise
        raw = ""
        out_path = path
    result = apply_patch_to_content(raw, patch_text)
    if "error" in result:
        raise ValueError(str(result["error"]))
    write_file(
        workplace,
        {"path": path, "content": str(result["content"]), "mode": "overwrite"},
    )
    return {
        "ok": True,
        "path": out_path,
        "hunks_applied": int(result.get("hunks_applied") or 0),
    }


def delete_file(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    path = _remote_path(workplace, str(params.get("path") or ""))
    client = connect(workplace)
    try:
        sftp = client.open_sftp()
        try:
            sftp.remove(path)
        finally:
            sftp.close()
    finally:
        client.close()
    return {"ok": True, "path": path}


def search_files(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Search via remote python one-liner for portability."""
    pattern = params.get("pattern") or ""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("'pattern' must be non-empty")
    # Fall back to bash grep for simplicity.
    root = _remote_root(workplace)
    glob_pat = params.get("glob") or ""
    # Match local search_files: regex by default; regex=false → fixed-string.
    if "regex" in params:
        use_regex = bool(params.get("regex"))
    else:
        use_regex = True
    # Safe-ish: pattern via env on remote.
    if use_regex:
        grep = f"grep -RInE -- {shlex.quote(pattern)} . 2>/dev/null | head -50"
    else:
        grep = f"grep -RInF -- {shlex.quote(pattern)} . 2>/dev/null | head -50"
    if glob_pat and isinstance(glob_pat, str):
        grep = f"find . -name {shlex.quote(glob_pat)} -type f -print0 2>/dev/null | xargs -0 grep -nH -- {shlex.quote(pattern)} 2>/dev/null | head -50"
    result = exec_bash(workplace, {"script": grep, "cwd": root, "timeout": 60})
    lines = [ln for ln in (result.get("stdout") or "").splitlines() if ln.strip()]
    return {"matches": lines, "count": len(lines), "capped": len(lines) >= 50}


def process_start(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    command = (params.get("command") or params.get("script") or "").strip()
    if not command:
        raise ValueError("'command' is required")
    cwd = (params.get("cwd") or "").strip() or _remote_root(workplace)
    # Detached job with pid-named logs under /tmp.
    script = (
        f"cd {shlex.quote(cwd)} || exit 1\n"
        f"nohup bash -lc {shlex.quote(command)} "
        f">/tmp/tomo_bg_new.out 2>/tmp/tomo_bg_new.err &\n"
        f"PID=$!\n"
        f"mv /tmp/tomo_bg_new.out /tmp/tomo_bg_$PID.out 2>/dev/null || true\n"
        f"mv /tmp/tomo_bg_new.err /tmp/tomo_bg_$PID.err 2>/dev/null || true\n"
        f"echo $PID\n"
    )
    client = connect(workplace)
    try:
        _stdin, stdout, stderr = client.exec_command(script, timeout=30)
        out = stdout.read().decode("utf-8", errors="replace").strip().splitlines()
        err = stderr.read().decode("utf-8", errors="replace")
        pid = out[-1].strip() if out else ""
        if not pid.isdigit():
            raise RuntimeError(err or f"failed to start background job: {out!r}")
    finally:
        client.close()
    return {"id": f"ssh_{pid}", "status": "running", "command": command, "pid": pid}


def process_list(workplace: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    _ = params
    result = exec_bash(
        workplace,
        {
            "script": "ps -eo pid,cmd | grep -E '[b]ash -lc' | head -20 || true",
            "timeout": 15,
        },
    )
    jobs = []
    for line in (result.get("stdout") or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            jobs.append(
                {
                    "id": f"ssh_{parts[0]}",
                    "status": "running",
                    "command": parts[1],
                    "returncode": None,
                }
            )
    return jobs


def process_status(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    jid = str(params.get("id") or "")
    pid = jid.removeprefix("ssh_")
    if not pid.isdigit():
        raise ValueError(f"unknown job id {jid!r}")
    result = exec_bash(
        workplace,
        {
            "script": (
                f"if kill -0 {pid} 2>/dev/null; then echo RUNNING; "
                f"else echo EXITED; fi; "
                f"echo '---stdout---'; cat /tmp/tomo_bg_{pid}.out 2>/dev/null; "
                f"echo '---stderr---'; cat /tmp/tomo_bg_{pid}.err 2>/dev/null"
            ),
            "timeout": 15,
        },
    )
    out = result.get("stdout") or ""
    status = "running" if out.startswith("RUNNING") else "exited"
    stdout = ""
    stderr = ""
    if "---stdout---" in out:
        rest = out.split("---stdout---", 1)[1]
        if "---stderr---" in rest:
            stdout, stderr = rest.split("---stderr---", 1)
        else:
            stdout = rest
    return {
        "id": jid,
        "status": status,
        "returncode": None if status == "running" else 0,
        "command": "",
        "stdout": _clip(stdout.strip()),
        "stderr": _clip(stderr.strip()),
    }


def process_kill(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    jid = str(params.get("id") or "")
    pid = jid.removeprefix("ssh_")
    if not pid.isdigit():
        raise ValueError(f"unknown job id {jid!r}")
    exec_bash(workplace, {"script": f"kill {pid} 2>/dev/null || true", "timeout": 10})
    return process_status(workplace, {"id": jid})


def _mkdir_p(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    parts = remote_dir.strip("/").split("/")
    cur = "" if remote_dir.startswith("/") else ""
    for part in parts:
        if not part:
            continue
        cur = f"{cur}/{part}" if cur else (f"/{part}" if remote_dir.startswith("/") else part)
        try:
            sftp.stat(cur)
        except OSError:
            try:
                sftp.mkdir(cur)
            except OSError:
                pass


_HANDLERS = {
    "exec_bash": exec_bash,
    "bash": exec_bash,
    "exec_python": exec_python,
    "read_file": read_file,
    "write_file": write_file,
    "read_file_b64": read_file_b64,
    "write_file_b64": write_file_b64,
    "str_replace": str_replace,
    "patch": patch_file,
    "delete_file": delete_file,
    "search_files": search_files,
    "process_start": process_start,
    "process_list": process_list,
    "process_status": process_status,
    "process_kill": process_kill,
}


def call(workplace: dict[str, Any], method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run method on SSH workplace; return ``{ok, result|error}``."""
    handler = _HANDLERS.get(method)
    if handler is None:
        return {"ok": False, "error": f"unknown method: {method}"}
    try:
        result = handler(workplace, params or {})
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


__all__ = ["call", "connect", "exec_bash"]
