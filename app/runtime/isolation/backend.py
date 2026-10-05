"""Container-per-chat CLI broker, with atomic policy admission and teardown.

Only a single coordinator process may control this runtime namespace. A host
flock enforces that invariant; use a distinct namespace for independent stores.
The runtime must be local: bind sources and filesystem quota checks describe
THIS host, never a remote Docker daemon. No server environment enters exec.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.runtime.access import AccessDenied, AccessUnavailable, ExecutionContext


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class Environment:
    name: str
    context: ExecutionContext
    fingerprint: str
    disk_filesystems: dict[int, int]
    scratch_mb: int
    last_used: float = field(default_factory=time.monotonic)
    processes: set = field(default_factory=set)
    busy: bool = False
    job_id: str | None = None
    held: int = 0
    # Held interactive terminals keep this environment alive across
    # one-shot actions. A completed action must not kill an unrelated
    # active terminal/job sharing the chat container; revocation removes
    # the container (killing holders) before reporting success.


class ContainerBackend:
    def __init__(self, *, policy=None, runtime: str | None = None,
                 image: str | None = None, namespace: str = "tomo", idle_seconds: int = 600):
        self.policy = policy
        self.runtime = runtime or os.environ.get("TOMO_SANDBOX_RUNTIME", "docker")
        self.image = image or os.environ.get("TOMO_SANDBOX_IMAGE", "tomo:sandbox")
        self.namespace = hashlib.sha256(namespace.encode()).hexdigest()[:16]
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._environments: dict[str, Environment] = {}
        self._lease = None
        self._ready = False
        self._closed = False

    @property
    def access(self):
        if self.policy is not None:
            return self.policy
        from app.services.access import access
        return access

    @staticmethod
    def _env() -> dict[str, str]:
        # CLI uses the local socket, not user's Docker context or SSH endpoint.
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
                "LANG": "C.UTF-8"}

    def _cli(self, args: list[str], *, timeout: float = 30) -> str:
        try:
            result = subprocess.run([self.runtime, *args], env=self._env(),
                                    capture_output=True, text=True, timeout=timeout,
                                    stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AccessUnavailable("Local container runtime is unavailable") from exc
        if result.returncode:
            # Daemon errors often contain protected host paths; do not echo them.
            raise AccessUnavailable("Local container runtime rejected the operation")
        return result.stdout

    def _known_idle(self, session_id: str) -> bool:
        rows = self.access.store.with_db(lambda c: dict(c.execute(
            "SELECT session_id,pending FROM container_admissions WHERE namespace=? AND session_id IN ('',?)",
            (self.namespace, session_id),
        ).fetchall()))
        # A specific uncertain admission overrides a clean namespace marker.
        return rows.get(session_id, rows.get('', 1)) == 0

    def _record_admission(self, session_id: str, *, pending: bool) -> None:
        def record(c):
            c.execute("INSERT INTO container_admissions(namespace,session_id,pending) VALUES (?,?,?) "
                      "ON CONFLICT(namespace,session_id) DO UPDATE SET pending=excluded.pending",
                      (self.namespace, session_id, int(pending)))
            c.commit()
        self.access.store.with_db(record)

    def _record_empty_namespace(self) -> None:
        # Only after real inventory recovery, or an explicit offline operator
        # recovery. Never inferred from root, missing Docker or an empty dict.
        def record(c):
            c.execute("DELETE FROM container_admissions WHERE namespace=?", (self.namespace,))
            c.execute("INSERT INTO container_admissions(namespace,session_id,pending) VALUES (?,'',0)", (self.namespace,))
            c.commit()
        self.access.store.with_db(record)

    def _recover_namespace(self) -> None:
        query = ["ps", "-aq", "--filter", f"label=org.tomo.sandbox.namespace={self.namespace}"]
        orphans = self._cli(query).split()
        if orphans:
            self._cli(["rm", "-f", *orphans])
        if self._cli(query).strip():
            raise AccessUnavailable("Local sandbox orphan teardown is unconfirmed")
        self._record_empty_namespace()

    def _initialize(self):
        if self._closed:
            # A previous shutdown released the lease and removed every
            # environment; a later startup (supervisor restart, or test
            # lifespans sharing one process) re-acquires and re-verifies
            # from scratch instead of staying permanently unavailable.
            self._closed = False
            self._ready = False
            self._lease = None
            self._environments = {}
        if self._ready:
            return
        if self.runtime not in ("docker", "podman", "/usr/bin/docker", "/usr/bin/podman"):
            raise AccessUnavailable("Unsupported local container runtime")
        # Root in the execution environment is forbidden, even for host root.
        if os.getuid() == 0:
            raise AccessUnavailable("Sandbox broker must run as a non-root account")
        lease_path = Path(tempfile.gettempdir()) / f"tomo-sandbox-{os.getuid()}-{self.namespace}.lock"
        fd = os.open(lease_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        lease = os.fdopen(fd, "w")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lease.close()
            raise AccessUnavailable("Local sandbox namespace already has a supervisor") from exc
        self._lease = lease
        try:
            # Recovery is cleanup, not admission. Remove retained execution
            # before checking NEW image/cgroup capabilities, which may have
            # become unavailable since the previous coordinator process.
            self._recover_namespace()
            info = json.loads(self._cli(["info", "--format", "json"]))
            if "podman" in self.runtime:
                controllers = info.get("host", {}).get("cgroupControllers", [])
                if not {"cpu", "memory", "pids"} <= set(controllers):
                    raise AccessUnavailable("Local runtime cannot enforce aggregate resource limits")
                if not info.get("host", {}).get("security", {}).get("seccompEnabled"):
                    raise AccessUnavailable("Local runtime requires a seccomp boundary")
            else:
                if any(info.get(key) is not True for key in ("MemoryLimit", "PidsLimit", "CpuCfsQuota", "CpuCfsPeriod")):
                    raise AccessUnavailable("Local runtime cannot enforce resource limits")
                security = " ".join(info.get("SecurityOptions", []))
                if "seccomp" not in security:
                    raise AccessUnavailable("Local runtime requires a seccomp boundary")
            # Use image ID, never pull or follow a moving tag during execution.
            image = json.loads(self._cli(["image", "inspect", self.image]))[0]
            if image.get("Config", {}).get("Labels", {}).get("org.tomo.sandbox.contract") != "1":
                raise AccessUnavailable("Local full-toolchain sandbox image is unavailable")
            self._image_id = image["Id"]
            self._ready = True
            threading.Thread(target=self._reap_idle, daemon=True, name="sandbox-idle").start()
        except Exception:
            self._lease.close()
            self._lease = None
            raise

    def _destination(self, context: ExecutionContext):
        if context.execution_mode != "restricted":
            raise AccessDenied("Container dispatch requires restricted execution")
        mounted = [r for r in context.resources if not r.transfer_only]
        if not mounted or any(r.kind != "local" or r.destination_id != "local" for r in mounted):
            raise AccessUnavailable("Selected destination lacks the restricted container capability; remote transport is not isolation")

    def _mounts(self, context: ExecutionContext) -> tuple[list[str], dict[int, int], str]:
        mounts, capacities, fingerprint = [], {}, []
        roots: list[Path] = []
        # Transfer-only endpoints live on other machines; they are never
        # mounted at the execution destination.
        for resource in [r for r in context.resources if not r.transfer_only]:
            root = Path(resource.root_path)
            target = f"/workplaces/{resource.workplace_id}"
            if (not root.is_absolute() or not root.is_dir() or root.resolve() != root
                    or resource.mount_path != target or "," in str(root)
                    or not resource.workplace_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in resource.workplace_id)):
                raise AccessUnavailable("Selected resource cannot be mounted safely")
            if resource.storage_kind not in ("personal", "project"):
                # Arbitrary host trees may contain live host sockets, devices or
                # submounts. Import into managed storage rather than expose them.
                raise AccessUnavailable("Restricted external host folders require managed-storage import")
            if any(root == old or root in old.parents or old in root.parents for old in roots):
                raise AccessDenied("Overlapping resource mounts are unavailable")
            roots.append(root)
            stat = root.stat()
            if resource.writable:
                fs = os.statvfs(root)
                # A hard conservative capacity bound, NOT du polling. Operators
                # normally supply bounded filesystems/project storage. An entire
                # large backing filesystem consumes its entire capacity budget.
                capacities[stat.st_dev] = math.ceil(fs.f_blocks * fs.f_frsize / (1024 * 1024))
            option = f"type=bind,src={root},dst={target},bind-recursive=disabled"
            if not resource.writable:
                option += ",readonly"
            mounts.extend(["--mount", option])
            fingerprint.append((resource.workplace_id, str(root), stat.st_dev, stat.st_ino, resource.permission))
        return mounts, capacities, hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()

    def _ensure(self, context: ExecutionContext, *, job_id: str | None = None, durable: bool = False) -> Environment:
        self._destination(context)
        # Shared aggregate ledger (see app/runtime/ledger.py): container
        # environments draw from the same per-user total as turns, host
        # processes, background work and terminals. Checked under the
        # backend lock so check-and-reserve is atomic for this backend.
        from app.runtime import ledger
        if job_id and any(e.job_id == job_id for e in self._environments.values()
                           if e.context.user_id == context.user_id):
            # Durable reservation made by reserve_job(): already admitted.
            pass
        elif job_id or durable:
            # Durable work (queued jobs, held interactive terminals) outlives
            # any single turn and always takes its own aggregate slot; it
            # must never share a live turn via within_session.
            ledger.check(context.user_id, context.quota)
        else:
            # Synchronous exec inside the caller's admitted turn shares it.
            ledger.check(context.user_id, context.quota, within_session=context.session_id)
        self._initialize()
        mounts, capacities, fingerprint = self._mounts(context)
        signature = f"{context.access_generation}:{fingerprint}:{context.quota}"
        previous = self._environments.get(context.session_id)
        if previous and previous.fingerprint != signature:
            self._remove(previous)
            previous = None
        if previous:
            if previous.busy and not (job_id and previous.job_id == job_id and not previous.processes):
                raise AccessUnavailable("Selected chat already has managed work running")
            return previous
        others = [e for e in self._environments.values() if e.context.user_id == context.user_id]
        quota = context.quota
        if len(others) >= quota.max_concurrent_jobs:
            # Idle environments do not retain admission slots indefinitely.
            idle = next((e for e in others if not e.busy), None)
            if idle:
                self._remove(idle)
                others.remove(idle)
            else:
                raise AccessUnavailable("User aggregate concurrency limit reached")
        slots = quota.max_concurrent_jobs
        if quota.memory_mb < slots:
            raise AccessUnavailable("Memory quota is too small for the configured aggregate concurrency")
        scratch = min(256, max(1, quota.disk_mb // (slots * 4)))
        union = dict(capacities)
        # Include inactive persistent writable managed resources too. Otherwise
        # sequential chats on disjoint disks could each fill a separate quota.
        for visible in self.access.list_visible_workplaces(context.user_id):
            if visible["permission"] != "read_write" or visible["storage_kind"] not in ("personal", "project") or visible["kind"] != "local":
                continue
            resource = self.access.store.get_workplace(visible["id"])
            path = Path(resource["root_path"])
            fs = os.statvfs(path)
            union[path.stat().st_dev] = math.ceil(fs.f_blocks * fs.f_frsize / (1024 * 1024))
        for other in others:
            union.update(other.disk_filesystems)
        # Three tmpfs regions each with a hard limit; all live environments
        # reserve their entire capacity (including idle ones).
        if sum(union.values()) + (len(others) + 1) * scratch * 3 > quota.disk_mb:
            raise AccessUnavailable("Selected local storage lacks a disk capacity boundary within the user's aggregate quota")
        uid, gid = os.getuid(), os.getgid()
        name = f"tomo-chat-{self.namespace}-{hashlib.sha256(context.session_id.encode()).hexdigest()[:24]}"
        args = ["run", "-d", "--name", name, "--label", f"org.tomo.sandbox.namespace={self.namespace}",
                "--label", f"org.tomo.sandbox.user={hashlib.sha256(context.user_id.encode()).hexdigest()}",
                "--network", "none", "--ipc", "private", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--user", f"{uid}:{gid}",
                "--cpus", str(quota.cpu / slots), "--memory", f"{max(1, quota.memory_mb // slots)}m",
                "--memory-swap", f"{max(1, quota.memory_mb // slots)}m", "--pids-limit", "512",
                "--ulimit", "nofile=1024:1024", "--ulimit", "core=0:0", "--log-driver", "none",
                "--env", "HOME=/home/chat", "--env", "XDG_CACHE_HOME=/home/chat/.cache",
                "--env", "TMPDIR=/tmp", "--env", "PYTHONUNBUFFERED=1",
                "--env", "OPENBLAS_NUM_THREADS=1", "--env", "OMP_NUM_THREADS=1",
                "--env", "MKL_NUM_THREADS=1"]
        if "podman" in self.runtime:
            args.extend(["--userns", "keep-id"])
            # Docker's bind-recursive spelling is not supported by Podman.
            mounts = [m.replace(",bind-recursive=disabled", ",bind-propagation=rprivate") for m in mounts]
            # Do not mount host submounts under Podman either.
            for resource in [r for r in context.resources if not r.transfer_only]:
                prefix = str(Path(resource.root_path)) + "/"
                with open("/proc/self/mountinfo", encoding="utf-8") as entries:
                    targets = (re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4]) for line in entries)
                    if any(target.startswith(prefix) for target in targets):
                        raise AccessUnavailable("Resource contains nested host mounts")
        for path in ("/home/chat", "/tmp", "/dev/shm"):
            args.extend(["--tmpfs", f"{path}:rw,nosuid,nodev,size={scratch}m,uid={uid},gid={gid},mode=1777"])
        active = next(r for r in context.resources if r.workplace_id == context.active_workplace_id)
        args.extend([*mounts, "--workdir", active.mount_path, "--entrypoint", "/bin/sleep", self._image_id, "infinity"])
        # Commit uncertainty BEFORE run: a CLI timeout or coordinator crash
        # can leave a real container even when no in-memory handle survives.
        self._record_admission(context.session_id, pending=True)
        environment = Environment(name, context, signature, capacities, scratch)
        self._environments[context.session_id] = environment
        try:
            self._cli(args)
        except Exception:
            # Track uncertain admission before cleanup. Failed cleanup keeps the
            # handle fenced for stopper retry; never pretend it was terminated.
            self._remove(environment)
            raise
        return environment

    def reserve_job(self, context: ExecutionContext, job_id: str) -> None:
        """Reserve real capacity/mounts BEFORE a queued job handle is registered."""
        with self.access.execution_guard(context) as current:
            with self._lock:
                environment = self._ensure(current)
                environment.busy, environment.job_id = True, job_id

    def stop_job(self, session_id: str, job_id: str) -> None:
        """Confirm only this job's teardown; an old handle cannot kill new work."""
        with self._lock:
            environment = self._environments.get(session_id)
            if environment and environment.job_id == job_id:
                environment.job_id = None
                environment.busy = False
                if not environment.held:
                    self._remove(environment)
                else:
                    environment.last_used = time.monotonic()

    def hold(self, context: ExecutionContext) -> Environment:
        """Pin the chat container for an interactive terminal holder."""
        with self.access.execution_guard(context) as current:
            with self._lock:
                environment = self._ensure(current, durable=True)
                environment.held += 1
                environment.last_used = time.monotonic()
                return environment

    def unhold(self, session_id: str, environment=None) -> None:
        """Release one terminal hold; an idle unheld environment is reaped."""
        with self._lock:
            current = self._environments.get(session_id)
            if environment is not None and current is not environment:
                # The holder's container was already revoked/replaced; its
                # hold died with it and must not debit the replacement.
                return
            if current and current.held:
                current.held -= 1
                current.last_used = time.monotonic()

    def execute(self, context: ExecutionContext, argv: list[str], *, cwd: str | None = None,
                timeout: float = 30, stdin: str | None = None, cancel_event=None,
                job_id: str | None = None) -> Result:
        if not argv or any(not isinstance(a, str) or "\x00" in a for a in argv):
            raise AccessDenied("Invalid sandbox command")
        if not math.isfinite(float(timeout)) or timeout <= 0:
            raise AccessDenied("Invalid sandbox duration")
        # Lock ordering is policy -> backend, also used by mutation stoppers.
        with self.access.execution_guard(context) as current:
            with self._lock:
                if cancel_event is not None and cancel_event.is_set():
                    raise AccessUnavailable("Selected container job was stopped before admission")
                reserved = self._environments.get(current.session_id)
                if job_id and (not reserved or reserved.job_id != job_id):
                    raise AccessUnavailable("Selected container job no longer holds admission")
                environment = self._ensure(current, job_id=job_id)
                active = next(r for r in current.resources if r.workplace_id == current.active_workplace_id)
                working = cwd or active.mount_path
                if not working.startswith("/") or "\x00" in working:
                    raise AccessDenied("Invalid container working directory")
                try:
                    proc = subprocess.Popen([self.runtime, "exec", "-i", "--workdir", working,
                                             environment.name, *argv], env=self._env(),
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            start_new_session=True)
                except OSError as exc:
                    raise AccessUnavailable("Selected local container cannot execute") from exc
                environment.processes.add(proc)
                environment.busy = True
        deadline = min(float(timeout), float(current.quota.duration_seconds))
        try:
            out, err = self._communicate_bounded(proc, stdin, deadline)
            # Kill the entire chat after each action. Shell & Python can fork
            # detached children: docker exec ending doesn't prove they ended.
            # Persistent project files survive; writable home/cache may reset.
            # Held interactive terminals share this container, so a completed
            # action keeps a held environment alive instead of killing an
            # unrelated active terminal. Shared fate is explicit: revocation
            # removes the container and kills every holder with it.
            return Result(proc.returncode, out, err)
        except subprocess.TimeoutExpired as exc:
            raise AccessUnavailable(f"Selected local container command exceeded {deadline:g}s and was terminated") from exc
        finally:
            with self._lock:
                if self._environments.get(current.session_id) is environment:
                    environment.processes.discard(proc)
                    if proc.poll() is None:
                        # A timed-out action must not keep running inside the
                        # shared held container; kill only this action's own
                        # process group (holders are untouched).
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            pass
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        if pipe:
                            try:
                                pipe.close()
                            except (OSError, ValueError):
                                pass
                    if environment.held:
                        environment.busy = False
                        environment.last_used = time.monotonic()
                    else:
                        self._remove(environment)
            self.access.audit(current.user_id, "sandbox.execute", session_id=current.session_id,
                              agent_id=current.agent_id, destination_id=current.destination_id,
                              outcome="ok" if proc.returncode == 0 else "failed")

    @staticmethod
    def _communicate_bounded(proc, stdin: str | None, timeout: float) -> tuple[str, str]:
        # Drain output concurrently, discard beyond budget rather than letting
        # arbitrary command output exhaust the coordinator's RAM.
        buffers = [bytearray(), bytearray()]
        def drain(pipe, buffer):
            try:
                while data := pipe.read(8192):
                    if len(buffer) < 100_000:
                        buffer.extend(data[:100_000 - len(buffer)])
            except (OSError, ValueError):
                pass  # confirmed stopper may close a pipe during cancellation
        readers = [threading.Thread(target=drain, args=(pipe, buf), daemon=True)
                   for pipe, buf in zip((proc.stdout, proc.stderr), buffers)]
        for reader in readers:
            reader.start()
        def feed():
            try:
                if stdin is not None:
                    proc.stdin.write(stdin.encode())
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        proc.wait(timeout=timeout)
        for thread in [*readers, writer]:
            thread.join(timeout=1)
        return tuple(bytes(buf).decode("utf-8", "replace") for buf in buffers)

    def _remove(self, environment: Environment):
        # rm -f kills all container descendants and removes all bind mounts.
        # A failure keeps the tracked handle and the policy barrier pending.
        query = ["ps", "-aq", "--filter", f"name={environment.name}"]
        if self._cli(query).strip():
            self._cli(["rm", "-f", environment.name])
        remaining = self._cli(query).strip()
        if remaining:
            raise AccessUnavailable("Selected local container teardown is unconfirmed")
        for proc in environment.processes:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=5)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe:
                    pipe.close()
        self._record_admission(environment.context.session_id, pending=False)
        if self._environments.get(environment.context.session_id) is environment:
            del self._environments[environment.context.session_id]

    def startup(self) -> None:
        """Recover orphaned execution and verify the required local capability."""
        with self._lock:
            self._initialize()

    def stop_session(self, session_id: str) -> None:
        with self._lock:
            environment = self._environments.get(session_id)
            if environment:
                self._remove(environment)
            elif not self._known_idle(session_id):
                # Known-idle host-only chats do not depend on Docker. Unknown
                # history/uncertain admission still requires real confirmation,
                # including after restart; runtime failure keeps policy pending.
                name = f"tomo-chat-{self.namespace}-{hashlib.sha256(session_id.encode()).hexdigest()[:24]}"
                query = ["ps", "-aq", "--filter", f"label=org.tomo.sandbox.namespace={self.namespace}", "--filter", f"name={name}"]
                handles = self._cli(query).split()
                if handles:
                    self._cli(["rm", "-f", *handles])
                if self._cli(query).strip():
                    raise AccessUnavailable("Selected local container recovery teardown is unconfirmed")
                self._record_admission(session_id, pending=False)

    def _reap_idle(self):
        while not self._closed:
            time.sleep(min(10, max(1, self.idle_seconds)))
            with self._lock:
                for environment in list(self._environments.values()):
                    # Held interactive terminals own their lifetime through
                    # the terminal supervisor (idle + duration caps); the
                    # action reaper must not reap a container out from under
                    # an attached terminal.
                    if environment.held:
                        continue
                    if not environment.busy and time.monotonic() - environment.last_used >= self.idle_seconds:
                        try:
                            self._remove(environment)
                        except AccessUnavailable:
                            pass  # retain failed handles; never report revoked

    def close(self):
        with self._lock:
            for environment in list(self._environments.values()):
                self._remove(environment)
            self._closed = True
            if self._lease:
                self._lease.close()
                self._lease = None


backend = ContainerBackend(namespace=os.environ.get("TOMO_SANDBOX_NAMESPACE", "tomo"))
