"""SSH workplace execution via Paramiko (agent tools).

Mirrors connector RPC shapes as Python return values that
:mod:`app.runtime.tools.workplace_remote` formats for the agent loop.
"""

from __future__ import annotations

import base64
import io
import json
import hashlib
import uuid
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


# This program runs on the SSH host. A detached supervisor owns one command
# process group and private spool; no OS-wide jobs are adopted by Tomo.
_SSH_JOBS_PROGRAM = r"""
import fcntl, json, os, pathlib, selectors, shutil, signal, subprocess, sys, time

request = json.loads(sys.argv[1])
base = pathlib.Path.home() / ".cache" / "tomo-jobs" / request["namespace"]
base.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(base, 0o700)
job_id = request.get("id", "")
job = base / job_id
cap = 512 * 1024

def publish(record):
    target = job / "meta.json"
    temporary = job / ("meta." + str(os.getpid()) + ".tmp")
    temporary.write_text(json.dumps(record))
    temporary.replace(target)

def snapshot(path):
    try:
        record = json.loads((path / "meta.json").read_text())
    except FileNotFoundError:
        return {"id": path.name, "status": "unknown", "returncode": None,
                "reason": "remote job handle missing", "process_contract": 1}
    for name in ("stdout", "stderr"):
        try:
            record[name] = (path / name).read_bytes()[-cap:].decode("utf-8", "replace")
        except FileNotFoundError:
            record[name] = ""
    if record["status"] in ("starting", "running", "stopping"):
        try:
            os.kill(record["supervisor_pid"], 0)
        except KeyError:
            if record["status"] != "starting" or time.time() - record["started_at"] > 10:
                record.update(status="unknown", reason="remote supervisor lost")
        except ProcessLookupError:
            record.update(status="unknown", reason="remote supervisor lost")
    return record

def members_alive(pgid):
    # Zombies are no longer running and may await init reaping. Do not mistake
    # those for surviving children; check every non-zombie group member.
    rows = subprocess.check_output(["ps", "-eo", "pgid=,stat="], text=True, timeout=.5)
    return any(int(parts[0]) == pgid and not parts[1].startswith("Z")
               for line in rows.splitlines() if len(parts := line.split()) == 2)

def cleanup(pgid):
    for sig, duration in ((signal.SIGTERM, .3), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        end = time.monotonic() + duration
        while time.monotonic() < end:
            if not members_alive(pgid):
                return True
            time.sleep(.025)
    return not members_alive(pgid)

def process_identity(pid):
    try:
        return subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True, timeout=.5).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ''

def supervise():
    record = json.loads((job / "meta.json").read_text())
    record["supervisor_pid"] = os.getpid()
    try:
        child = subprocess.Popen(["bash", "-lc", record["command"]],
            cwd=record["cwd"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True)
    except Exception as exc:
        record.update(status="failed", returncode=-1, reason=str(exc), finished_at=time.time())
        publish(record)
        return
    record.update(status="running", pid=child.pid, pid_identity=process_identity(child.pid))
    publish(record)
    selector = selectors.DefaultSelector()
    tails = {"stdout": bytearray(), "stderr": bytearray()}
    for stream, name in ((child.stdout, "stdout"), (child.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    stopped = False
    cleanup_ok = True
    next_flush = [0.0]
    try:
        while child.poll() is None:
            if (job / "stop").exists():
                # poll again: a late stop must preserve the command result.
                if child.poll() is None:
                    record["status"] = "stopping"
                    publish(record)
                    stopped = True
                    cleanup_ok = cleanup(child.pid)
                break
            drain(selector, tails, record, .05, next_flush)
        rc = child.wait(timeout=2)
        cleanup_ok = cleanup(child.pid) and cleanup_ok
        deadline = time.monotonic() + .5
        while selector.get_map() and time.monotonic() < deadline:
            drain(selector, tails, record, .025, next_flush)
        for name, tail in tails.items():
            (job / name).write_bytes(tail)
        record.update(status=("stopped" if stopped and rc < 0 else "exited") if cleanup_ok else "unknown",
                      returncode=rc if cleanup_ok else None, finished_at=time.time())
        if not cleanup_ok:
            record["reason"] = "process group cleanup could not be confirmed"
        publish(record)
    except Exception as exc:
        try:
            cleanup(child.pid)
        except Exception:
            pass
        record.update(status="unknown", returncode=None, reason=str(exc))
        publish(record)
    finally:
        selector.close()
        child.stdout.close()
        child.stderr.close()

def drain(selector, tails, record, timeout, next_flush):
    changed = False
    for key, _ in selector.select(timeout):
        data = os.read(key.fileobj.fileno(), 65536)
        if not data:
            selector.unregister(key.fileobj)
            continue
        name = key.data
        tail = tails[name]
        tail.extend(data)
        if len(tail) > cap:
            del tail[:-cap]
            record["truncated"] = True
        changed = True
    if changed and time.monotonic() >= next_flush[0]:
        for name, tail in tails.items():
            (job / name).write_bytes(tail)
        publish(record)
        next_flush[0] = time.monotonic() + .15

op = request["op"]
if op == "supervise":
    supervise()
elif op == "contract":
    print(json.dumps({"process_contract": 1}))
elif op == "start":
    with (base / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if job.exists():
            record = snapshot(job)
            if record.get("command") != request["command"]:
                raise ValueError("job id already belongs to a different command")
        else:
            retained = []
            for path in base.iterdir():
                if not path.is_dir() or not path.name.startswith("ssh_"):
                    continue
                record = snapshot(path)
                if record["status"] in ("exited", "failed", "stopped") and record.get("finished_at") and time.time() - record["finished_at"] >= 900:
                    shutil.rmtree(path)
                else:
                    retained.append(record)
            if len(retained) >= 128 or sum(r["status"] in ("starting", "running", "stopping", "unknown") for r in retained) >= 16:
                raise ValueError("busy: background job limit reached")
            root = pathlib.Path(request["root"]).expanduser().resolve(strict=True)
            cwd = pathlib.Path(request.get("cwd") or str(root)).expanduser()
            if str(cwd) == request["root"]:
                cwd = root
            if not cwd.is_absolute():
                cwd = root / cwd
            cwd = cwd.resolve(strict=True)
            cwd.relative_to(root)
            if not cwd.is_dir():
                raise ValueError("cwd is not a directory")
            job.mkdir(mode=0o700)
            record = {"id": job_id, "status": "starting", "returncode": None,
                      "command": request["command"], "cwd": str(cwd), "started_at": time.time(),
                      "process_contract": 1, "truncated": False}
            publish(record)
            child_request = dict(request, op="supervise")
            supervisor = subprocess.Popen([sys.executable, "-c", request["program"], json.dumps(child_request)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            record["supervisor_pid"] = supervisor.pid
            # The supervisor publishes its own pid. Do not overwrite its state.
        print(json.dumps(record))
elif op == "list":
    print(json.dumps([snapshot(path) for path in base.iterdir()
                     if path.is_dir() and path.name.startswith("ssh_")]))
elif op in ("status", "kill"):
    record = snapshot(job)
    if op == "kill" and record["status"] in ("starting", "running", "stopping"):
        (job / "stop").touch()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            record = snapshot(job)
            if record["status"] not in ("starting", "running", "stopping"):
                break
            time.sleep(.05)
    if op == "kill" and record["status"] == "unknown" and record.get("pid"):
        # A stale PID alone is never authority to stop an unrelated new process.
        pid = record['pid']
        if record.get('pid_identity') and process_identity(pid) == record['pid_identity']:
            stopped = cleanup(pid)
            record.update(stop_confirmed=stopped, reason='Process group stopped; original exit code unavailable' if stopped else 'Process group cleanup could not be confirmed')
            publish(record)
    print(json.dumps(record))
"""


class SSHJobRejected(RuntimeError):
    """Remote job operation was rejected before start acknowledgment."""


def _ssh_job_call(workplace: dict[str, Any], op: str, params: dict[str, Any]) -> Any:
    jid = str(params.get("id") or "")
    if op == "start" and not jid:
        jid = "ssh_" + uuid.uuid4().hex
    if op not in ("list", "contract"):
        import re

        if not re.fullmatch(r"ssh_[0-9a-f]{32}", jid):
            raise ValueError("invalid SSH background job handle")
    namespace = hashlib.sha256(
        str(workplace.get("id") or "").encode()
    ).hexdigest()[:24]
    request = dict(params, op=op, id=jid, namespace=namespace, root=_remote_root(workplace))
    if op == "start":
        request["program"] = _SSH_JOBS_PROGRAM
    wrapped = "python3 -c " + shlex.quote(_SSH_JOBS_PROGRAM) + " " + shlex.quote(json.dumps(request))
    client = connect(workplace)
    try:
        _stdin, stdout, stderr = client.exec_command(wrapped, timeout=10)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        if rc:
            raise SSHJobRejected(err.strip() or "SSH background job operation failed")
        return json.loads(out)
    finally:
        client.close()


def process_start(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    command = (params.get("command") or params.get("script") or "").strip()
    if not command:
        raise ValueError("'command' is required")
    return _ssh_job_call(workplace, "start", dict(params, command=command))


def process_list(workplace: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    return _ssh_job_call(workplace, "list", params)


def process_status(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    if params.get("id") == "__contract__":
        return _ssh_job_call(workplace, "contract", {})
    return _ssh_job_call(workplace, "status", params)


def process_kill(workplace: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    return _ssh_job_call(workplace, "kill", params)


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
