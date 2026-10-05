"""In-process test destination enforcing the remote execution contract.

FakeDestination plugs a fabricated connector session into the REAL hub, so
server admission, envelope construction, capability negotiation and RPC
dispatch run unmocked. Only the destination side is simulated — against
isolated temp dirs, with the same rules as the Go connector:

* ``exec_admit`` registers (owner, session) at a generation; every other
  execution RPC validates owner/session/destination/generation first;
* file/exec paths resolve under destination-owned ``<root>/<workplace>``
  scopes (coordinator paths are never trusted), read-only scopes reject
  writes, nothing escapes its root;
* background jobs are owner/session-tagged with a destination-enforced
  deadline; ``exec_teardown`` kills them and acknowledges;
* version/caps are configurable so version-mismatch and missing-capability
  refusals are exercised for real (old/unsupported/offline stay
  fail-closed; nothing falls back locally).

Network egress is NOT simulated: ``secret_http`` refuses, because this
destination has no network boundary to enforce.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app.workplaces.hub import ConnectorSession, hub
from app.workplaces.remote_contract import REMOTE_EXEC_CONTRACT

_SCOPE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CORR_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class DestinationRefused(RuntimeError):
    pass


class FakeJob:
    def __init__(self, job_id: str, owner: str, session: str, correlation: str,
                 command: str, cwd: str, proc: subprocess.Popen, deadline: float):
        self.id = job_id
        self.owner = owner
        self.session = session
        self.correlation = correlation
        self.command = command
        self.cwd = cwd
        self.proc = proc
        self.deadline = deadline
        self.started = time.time()
        self.stop_requested = False


class FakeDestination:
    def __init__(self, workplace_id: str, root: str | Path, *,
                 version: str = "0.4.0",
                 caps: str = "idempotent-replay,exec-stream,exec-context-v1",
                 sandbox_image: str = ""):
        self.workplace_id = workplace_id
        self.root = Path(root)
        self.version = version
        self.caps = caps
        self.sandbox_image = sandbox_image
        self._lock = threading.Lock()
        self._admitted: dict[tuple[str, str], dict[str, Any]] = {}
        self._jobs: dict[str, FakeJob] = {}
        self._counter = 0
        if "remote-sandbox-v1" in [c.strip() for c in caps.split(",")]:
            assert sandbox_image, "sandbox cap requires an image attestation"

    # -- hub plumbing ----------------------------------------------------
    def register(self) -> ConnectorSession:
        session = _FakeSession(self.workplace_id, self)
        hub.register(session)
        return session

    def unregister(self) -> None:
        hub.unregister(self.workplace_id)

    # -- contract ---------------------------------------------------------
    def _envelope(self, params: dict[str, Any]) -> dict[str, Any]:
        env = (params or {}).get("exec_context")
        if not isinstance(env, dict) or env.get("v") != 1:
            raise DestinationRefused("exec_context is required")
        for key in ("owner_user_id", "session_id", "destination_id"):
            if not str(env.get(key) or "").strip():
                raise DestinationRefused(f"exec_context missing {key}")
        if env.get("execution_mode") not in ("restricted", "unrestricted"):
            raise DestinationRefused("exec_context has unknown execution mode")
        if str(env["destination_id"]) != self.workplace_id:
            raise DestinationRefused("exec_context destination does not match this destination")
        try:
            gen = int(env.get("access_generation", 0))
        except (TypeError, ValueError):
            raise DestinationRefused("exec_context has invalid generation")
        if gen < 0:
            raise DestinationRefused("exec_context has invalid generation")
        return env

    def _roots(self, env: dict[str, Any]) -> dict[str, dict[str, Any]]:
        roots: dict[str, dict[str, Any]] = {}
        resources = env.get("resources") or []
        if not isinstance(resources, list):
            raise DestinationRefused("exec_context resources must be a list")
        for item in resources:
            if not isinstance(item, dict) or item.get("transfer_only"):
                continue
            scope = str(item.get("workplace_id") or "")
            if not _SCOPE_RE.fullmatch(scope):
                continue
            path = self.root / scope
            path.mkdir(parents=True, exist_ok=True)
            roots[scope] = {"path": path, "writable": item.get("permission") == "read_write"}
        return roots

    def _admission(self, method: str, params: dict[str, Any]) -> tuple[dict, dict]:
        if method == "ping":
            return {}, {}
        env = self._envelope(params)
        if method in ("exec_admit", "exec_teardown"):
            return env, self._roots(env)
        key = (str(env["owner_user_id"]), str(env["session_id"]))
        with self._lock:
            rec = self._admitted.get(key)
        if rec is None:
            raise DestinationRefused("no live execution admission for this session")
        if rec["destination"] != env["destination_id"] or rec["generation"] != int(env["access_generation"]):
            raise DestinationRefused("stale execution generation: access changed; re-admit")
        return env, self._roots(env)

    def _resolve(self, roots: dict, env: dict, path: str, *, write: bool) -> Path:
        text = (path or "").strip().replace("\x00", "")
        if not text:
            raise DestinationRefused("path must not be empty")
        active = str(env.get("active_workplace_id") or "")
        candidate: Path | None = None
        if os.path.isabs(text):
            target = Path(text)
            for scope, root in roots.items():
                try:
                    target.resolve().relative_to(root["path"].resolve())
                    candidate = root
                    break
                except (ValueError, OSError):
                    continue
            if candidate is None:
                raise DestinationRefused("path is outside admitted execution roots")
            resolved = target
        else:
            base = roots.get(active, {}).get("path") if active in roots else None
            if base is None:
                if not roots:
                    raise DestinationRefused("no admitted execution roots")
                base = next(iter(roots.values()))["path"]
            resolved = base / text
        if write and not candidate_writable(roots, resolved):
            raise DestinationRefused("path is read-only in this execution scope")
        # Contain symlinks like the Go connector.
        try:
            real = resolved.resolve() if resolved.exists() else _resolve_missing(resolved)
        except OSError:
            raise DestinationRefused("path escapes execution root")
        for root in roots.values():
            try:
                real.relative_to(root["path"].resolve())
                return real
            except (ValueError, OSError):
                continue
        raise DestinationRefused("path escapes execution root")

    # -- RPC --------------------------------------------------------------
    def handle(self, method: str, params: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
        try:
            if method == "ping":
                return {"ok": True, "result": "pong"}
            env, roots = self._admission(method, params)
            handler = {
                "exec_admit": self._exec_admit,
                "exec_teardown": self._exec_teardown,
                "exec_bash": self._exec, "bash": self._exec,
                "exec_python": self._exec_python,
                "read_file": self._read_file,
                "write_file": self._write_file,
                "read_file_b64": self._read_b64,
                "write_file_b64": self._write_b64,
                "process_start": self._job_start,
                "process_status": self._job_status,
                "process_kill": self._job_kill,
                "process_list": self._job_list,
                "list_dir": self._list_dir,
                "str_replace": self._str_replace,
                "patch": self._apply_patch,
                "delete_file": self._delete_file,
                "search_files": self._search_files,
            }.get(method)
            if handler is None:
                return {"ok": False, "error": f"unknown method: {method}"}
            return {"ok": True, "result": handler(env, roots, params or {}, timeout)}
        except DestinationRefused as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"destination failed: {exc}"}

    def _exec_admit(self, env, roots, params, timeout):
        key = (str(env["owner_user_id"]), str(env["session_id"]))
        with self._lock:
            self._admitted[key] = {
                "destination": env["destination_id"],
                "generation": int(env["access_generation"]),
                "mode": env["execution_mode"],
            }
        return {
            "contract": REMOTE_EXEC_CONTRACT,
            "mode": env["execution_mode"],
            "destination": self.workplace_id,
            "owner": env["owner_user_id"],
            "session": env["session_id"],
            "generation": int(env["access_generation"]),
            "sandbox": "remote-sandbox-v1" in [c.strip() for c in self.caps.split(",")],
            "sandbox_img": self.sandbox_image,
        }

    def _exec_teardown(self, env, roots, params, timeout):
        owner, session = str(env["owner_user_id"]), str(env["session_id"])
        killed = 0
        with self._lock:
            for job in list(self._jobs.values()):
                if job.owner == owner and job.session == session and job.proc.poll() is None:
                    try:
                        job.proc.terminate()
                        killed += 1
                    except Exception:
                        pass
            self._admitted.pop((owner, session), None)
        # Reap briefly so the ack reflects dead processes, not zombies.
        deadline = time.time() + 2.0
        with self._lock:
            targets = [j for j in self._jobs.values() if j.owner == owner and j.session == session]
        for job in targets:
            try:
                remaining = max(0.0, deadline - time.time())
                job.proc.wait(timeout=remaining)
            except Exception:
                try:
                    job.proc.kill()
                except Exception:
                    pass
        return {"torn_down": True, "jobs_killed": killed,
                "destination": self.workplace_id, "owner": owner, "session": session}

    def _scrub_env(self) -> dict[str, str]:
        out = dict(os.environ)
        for key in ("TOMO_BROKER_URL", "TOMO_BROKER_TOKEN", "TOMO_HTTP_BROKER",
                    "TOMO_HTTP_TOKEN", "TOMO_SECRET_KEY"):
            out.pop(key, None)
        return out

    def _exec(self, env, roots, params, timeout):
        script = str(params.get("script", "") or params.get("command", "")).strip()
        if not script:
            raise DestinationRefused("'script' (or 'command') is required")
        cwd = self._resolve(roots, env, str(params.get("cwd") or "."), write=False)
        cwd.mkdir(parents=True, exist_ok=True)
        cap = _quota_timeout(env, params.get("timeout"))
        try:
            proc = subprocess.run(["bash", "-s"], input=script, capture_output=True,
                                  text=True, cwd=str(cwd), env=self._scrub_env(), timeout=cap)
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            return {"stdout": out, "stderr": err + f"\nExecution timed out after {cap}s",
                    "exit_code": -1, "execution_time": float(cap)}
        return {"stdout": proc.stdout, "stderr": proc.stderr,
                "exit_code": proc.returncode, "execution_time": 0.0}

    def _exec_python(self, env, roots, params, timeout):
        code = str(params.get("code", "")).strip()
        if not code:
            raise DestinationRefused("'code' is required")
        return self._run_python(env, roots, params, timeout)

    def _run_python(self, env, roots, params, timeout):
        code = str(params.get("code", "")).strip()
        cwd = self._resolve(roots, env, str(params.get("cwd") or "."), write=False)
        cwd.mkdir(parents=True, exist_ok=True)
        cap = _quota_timeout(env, params.get("timeout"))
        try:
            proc = subprocess.run(["python3", "-"], input=code, capture_output=True,
                                  text=True, cwd=str(cwd), env=self._scrub_env(), timeout=cap)
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": f"\nExecution timed out after {cap}s",
                    "exit_code": -1, "execution_time": float(cap)}
        return {"stdout": proc.stdout, "stderr": proc.stderr,
                "exit_code": proc.returncode, "execution_time": 0.0}

    def _read_file(self, env, roots, params, timeout):
        target = self._resolve(roots, env, str(params.get("path", "")), write=False)
        if target.is_dir():
            raise DestinationRefused("path is a directory, not a file")
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            raise DestinationRefused(f"file not found: {params.get('path')}")
        return {"content": data.decode("utf-8", errors="replace"), "size": len(data), "path": str(target)}

    def _write_file(self, env, roots, params, timeout):
        target = self._resolve(roots, env, str(params.get("path", "")), write=True)
        content = params.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise DestinationRefused("'content' argument must be a string")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(target)}

    def _read_b64(self, env, roots, params, timeout):
        target = self._resolve(roots, env, str(params.get("path", "")), write=False)
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            raise DestinationRefused(f"file not found: {params.get('path')}")
        try:
            offset = max(0, int(params.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            size = int(params.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        chunk = data[offset:] if size <= 0 else data[offset:offset + size]
        return {"data": base64.b64encode(chunk).decode(), "bytes_read": len(chunk),
                "total_size": len(data), "path": str(target)}

    def _write_b64(self, env, roots, params, timeout):
        target = self._resolve(roots, env, str(params.get("path", "")), write=True)
        try:
            decoded = base64.b64decode(str(params.get("data") or ""))
        except Exception:
            raise DestinationRefused("base64 decode error")
        try:
            offset = max(0, int(params.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        is_last = params.get("is_last", True)
        if not isinstance(is_last, bool):
            is_last = True
        part = Path(str(target) + ".part")
        target.parent.mkdir(parents=True, exist_ok=True)
        if offset == 0:
            part.write_bytes(decoded)
        else:
            with part.open("ab") as handle:
                handle.write(decoded)
        if is_last:
            part.replace(target)
        return {"ok": True, "path": str(target)}

    def _list_dir(self, env, roots, params, timeout):
        from pathlib import Path as _Path
        raw = str(params.get("path", "") or ".").strip() or "."
        target = self._resolve(roots, env, raw, write=False)
        if not target.exists():
            raise DestinationRefused(f"path not found: {params.get('path')}")
        if not target.is_dir():
            raise DestinationRefused(f"not a directory: {params.get('path')}")
        try:
            recursive = bool(params.get("recursive"))
        except Exception:
            recursive = False
        try:
            max_depth = int(params.get("max_depth") or 3)
        except (TypeError, ValueError):
            max_depth = 3
        max_depth = min(max(max_depth, 0), 5)
        entries: list[dict[str, Any]] = []
        def walk(directory: _Path, depth: int) -> None:
            if depth > max_depth or len(entries) >= 200:
                return
            try:
                items = sorted(directory.iterdir(), key=lambda e: e.name)
            except OSError:
                return
            for item in items:
                if len(entries) >= 200:
                    break
                if item.name.startswith(".tomo"):
                    continue
                try:
                    rel = item.relative_to(target).as_posix()
                except ValueError:
                    continue
                if item.is_symlink():
                    kind = "link"
                elif item.is_dir():
                    kind = "dir"
                else:
                    kind = "file"
                try:
                    size = item.stat().st_size if kind == "file" else 0
                except OSError:
                    size = 0
                entries.append({"name": rel, "type": kind, "size": size, "depth": depth})
                if recursive and kind == "dir":
                    walk(item, depth + 1)
        walk(target, 0)
        return {"entries": entries, "capped": len(entries) >= 200, "path": str(target)}

    def _str_replace(self, env, roots, params, timeout):
        target = self._resolve(roots, env, str(params.get("path", "")), write=True)
        old = str(params.get("old_string", ""))
        new = params.get("new_string")
        if not isinstance(new, str) or not old:
            raise DestinationRefused("'new_string'/'old_string' are required")
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise DestinationRefused(f"file not found: {params.get('path')}")
        occ = text.count(old)
        if occ == 0:
            raise DestinationRefused("old_string not found in file")
        target.write_text(text.replace(old, new), encoding="utf-8")
        return {"ok": True, "path": str(target), "replacements": occ}

    def _apply_patch(self, env, roots, params, timeout):
        import re as _re
        target = self._resolve(roots, env, str(params.get("path", "")), write=True)
        patch_text = params.get("patch")
        if not isinstance(patch_text, str) or not patch_text.strip():
            raise DestinationRefused("'patch' argument must be a non-empty string")
        original = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        # Minimal unified-diff application (---/+++/@@ hunks with context).
        hunks: list[tuple[int, list[tuple[str, str]]]] = []
        current: tuple[int, list[tuple[str, str]]] | None = None
        for line in patch_text.splitlines():
            if line.startswith("@@"):
                match = _re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if not match:
                    raise DestinationRefused("invalid patch hunk header")
                current = (int(match.group(1)) - 1, [])
                hunks.append(current)
            elif current is not None and line[:1] in (" ", "-", "+", "\\"):
                if line.startswith("\\"):
                    continue
                current[1].append((line[:1], line[1:]))
        if not hunks:
            raise DestinationRefused("no hunks found in patch")
        applied = 0
        for start, ops in hunks:
            cursor = start
            for kind, text in ops:
                if kind == " ":
                    if cursor >= len(original) or original[cursor] != text:
                        raise DestinationRefused("patch context mismatch")
                    cursor += 1
                elif kind == "-":
                    if cursor >= len(original) or original[cursor] != text:
                        raise DestinationRefused("patch context mismatch")
                    original.pop(cursor)
                else:
                    original.insert(cursor, text)
                    cursor += 1
            applied += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(original) + ("\n" if original else ""), encoding="utf-8")
        return {"ok": True, "path": str(target), "hunks_applied": applied}

    def _delete_file(self, env, roots, params, timeout):
        target = self._resolve(roots, env, str(params.get("path", "")), write=True)
        try:
            if target.is_dir():
                raise DestinationRefused("path is a directory; delete_file only removes files")
            target.unlink()
        except FileNotFoundError:
            raise DestinationRefused(f"file not found: {params.get('path')}")
        return {"ok": True, "path": str(target)}

    def _search_files(self, env, roots, params, timeout):
        import re as _re
        pattern = str(params.get("pattern", ""))
        if not pattern:
            raise DestinationRefused("'pattern' argument must be a non-empty string")
        try:
            use_regex = bool(params.get("regex", True))
        except Exception:
            use_regex = True
        rx = _re.compile(pattern) if use_regex else None
        matches: list[str] = []
        for scope in roots.values():
            base = scope["path"]
            for path in sorted(base.rglob("*")):
                if len(matches) >= 50:
                    break
                if not path.is_file() or path.suffix == ".part":
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, ValueError):
                    continue
                rel = path.relative_to(base).as_posix()
                for i, line in enumerate(text.splitlines()):
                    hit = bool(rx.search(line)) if rx else (pattern in line)
                    if hit:
                        matches.append(f"{rel}:{i + 1}:{line.strip()[:200]}")
                        if len(matches) >= 50:
                            break
        return {"matches": matches, "count": len(matches), "capped": len(matches) >= 50}

    def _snapshot(self, job: FakeJob) -> dict[str, Any]:
        rc = job.proc.poll()
        if rc is None and time.time() > job.deadline:
            try:
                job.proc.terminate()
            except Exception:
                pass
            rc = job.proc.poll()
        status = "running" if rc is None else ("succeeded" if rc == 0 else "failed")
        if job.stop_requested and rc is not None and rc < 0:
            status = "stopped"
        return {"id": job.id, "remote_contract": REMOTE_EXEC_CONTRACT,
                "correlation_id": job.correlation, "status": status,
                "returncode": rc, "command": job.command}

    def _job_start(self, env, roots, params, timeout):
        command = str(params.get("command", "") or params.get("script", "")).strip()
        if not command:
            raise DestinationRefused("'command' must be a non-empty string")
        cwd = self._resolve(roots, env, str(params.get("cwd") or "."), write=False)
        cwd.mkdir(parents=True, exist_ok=True)
        owner, session = str(env["owner_user_id"]), str(env["session_id"])
        correlation = str(params.get("correlation_id", "") or "")
        if correlation and not _CORR_RE.fullmatch(correlation):
            raise DestinationRefused("invalid correlation id")
        cap = _quota_timeout(env, params.get("timeout"))
        with self._lock:
            if correlation:
                for job in self._jobs.values():
                    if (job.correlation == correlation and job.owner == owner
                            and job.session == session):
                        if job.command != command or job.cwd != str(cwd):
                            raise DestinationRefused("correlation id already belongs to a different command")
                        return self._snapshot(job)
            running = sum(1 for j in self._jobs.values() if j.proc.poll() is None)
            if running >= 16:
                raise DestinationRefused("busy: background job limit reached")
            self._counter += 1
            job_id = f"job_{self._counter:032x}"
            proc = subprocess.Popen(["bash", "-lc", command], cwd=str(cwd),
                                    env=self._scrub_env(), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
            job = FakeJob(job_id, owner, session, correlation, command, str(cwd),
                          proc, time.time() + cap)
            self._jobs[job_id] = job
            return self._snapshot(job)

    def _owned_job(self, env, job_id: str) -> FakeJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise DestinationRefused(f"unknown job id {job_id!r}")
        if job.owner != env["owner_user_id"] or job.session != env["session_id"]:
            raise DestinationRefused("job belongs to another execution scope")
        return job

    def _job_status(self, env, roots, params, timeout):
        job_id = str(params.get("id", ""))
        if job_id == "__contract__":
            return {"remote_contract": REMOTE_EXEC_CONTRACT}
        return self._snapshot(self._owned_job(env, job_id))

    def _job_kill(self, env, roots, params, timeout):
        job = self._owned_job(env, str(params.get("id", "")))
        job.stop_requested = True
        if job.proc.poll() is None:
            try:
                job.proc.terminate()
            except Exception:
                pass
            try:
                job.proc.wait(timeout=2.0)
            except Exception:
                try:
                    job.proc.kill()
                except Exception:
                    pass
        return self._snapshot(job)

    def _job_list(self, env, roots, params, timeout):
        with self._lock:
            jobs = [j for j in self._jobs.values()
                    if j.owner == env["owner_user_id"] and j.session == env["session_id"]]
        return [self._snapshot(j) for j in jobs]


class _FakeSession(ConnectorSession):
    """ConnectorSession whose RPCs execute against a FakeDestination."""

    def __init__(self, workplace_id: str, dest: FakeDestination):
        super().__init__(
            workplace_id, websocket=None, loop=None,  # type: ignore[arg-type]
            hostname="test-destination", version=dest.version,
            platform="test", replay_ok=True, stream_ok=True,
            secret_broker="secret-broker" in dest.caps, caps=dest.caps,
        )
        self.dest = dest

    def call(self, method: str, params: dict[str, Any] | None = None, *,
             timeout: float = 60.0, on_progress=None) -> dict[str, Any]:
        return self.dest.handle(method, params or {}, timeout)


def candidate_writable(roots: dict, resolved: Path) -> bool:
    for root in roots.values():
        try:
            resolved.relative_to(root["path"].resolve())
            return bool(root["writable"])
        except (ValueError, OSError):
            continue
    return False


def _resolve_missing(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise OSError("no existing ancestor")
        current = parent
    return current.resolve() / path.relative_to(current)


def _quota_timeout(env: dict[str, Any], want: Any) -> int:
    try:
        value = int(float(want)) if want is not None else 60
    except (TypeError, ValueError):
        value = 60
    value = min(max(value, 1), 600)
    try:
        quota = int(((env.get("quota") or {}).get("duration_seconds")) or 0)
    except (TypeError, ValueError):
        quota = 0
    if quota > 0:
        value = min(value, quota)
    return value


__all__ = ["FakeDestination", "DestinationRefused"]
