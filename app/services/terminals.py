"""Chat-owned interactive terminals: restricted container shells and host shells.

Restricted terminals run ``/bin/sh`` inside the chat's HELD container
environment (same authorized mounts, owner context, shared quota ledger,
duration caps, private home, kernel-enforced read-only mounts). Unrestricted
terminals remain host PTYs and require a matching Admin grant plus explicit
chat activation; the platform role is unchanged. No host PTY fallback exists
for restricted execution. Browser connections only attach to owned terminals.
"""

from __future__ import annotations

import asyncio
from collections import deque
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

# Keep Unix-only imports lazy so non-POSIX installations can still start Tomo.
MAX_OUTPUT = 1024 * 1024
MAX_PER_SESSION = 8
MAX_TOTAL = 64
IDLE_TIMEOUT = 30 * 60


class LocalTerminal:
    backend = "host"

    def __init__(self, session_id: str, cwd: Path, cols: int, rows: int, *, execution=None):
        from app.runtime.policy import require_session_service
        from app.services import store
        self.execution = require_session_service(session_id) if execution is None else store.access.revalidate(execution)
        if self.execution.session_id != session_id:
            raise PermissionError("Terminal session is unavailable")
        # Grant plus explicit chat activation are enforced when the context
        # is resolved; the platform role itself is unchanged and retained
        # for accountability. Any role with an unrestricted grant may hold
        # a host PTY at the granted destination.
        if self.execution.execution_mode != "unrestricted":
            raise PermissionError("Interactive sandbox terminal backend is unavailable; host PTY is forbidden")
        if any(r.kind != "local" for r in self.execution.resources):
            raise PermissionError("Remote terminal cannot execute on the local host")
        self.id = uuid.uuid4().hex
        self.session_id = session_id
        self.cwd = str(cwd)
        self.shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
        self._init_pty(cols, rows)

    def _init_pty(self, cols: int, rows: int, *, raw: bool = False) -> None:
        """Allocate the server PTY and spawn the shell; raw defers echo to a container tty."""
        import fcntl
        import pty
        import struct
        import termios

        self.created = time.time()
        self.last_activity = self.created
        self.exit_code: int | None = None
        self.output: deque[bytes] = deque()
        self.output_size = 0
        self.listeners: set[asyncio.Queue] = set()
        self.loop = asyncio.get_running_loop()
        self.fd, slave = pty.openpty()
        try:
            if raw:
                import tty

                # The container allocates its own tty (see ContainerTerminal)
                # which echoes; a second server-side echo would double every
                # keystroke and a server-side line discipline would delay
                # bytes the container tty must see immediately.
                tty.setraw(slave)
            fcntl.ioctl(
                slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0)
            )
            self._spawn(slave)
            os.set_blocking(self.fd, False)
            self.loop.add_reader(self.fd, self._read)
        except BaseException:
            if getattr(self, "process", None) is not None:
                try:
                    self.process.kill()
                    self.process.wait()
                except Exception:
                    pass
            os.close(self.fd)
            self.fd = None
            raise
        finally:
            os.close(slave)
        self.watcher = self.loop.create_task(self._watch())
        self.cleanup: asyncio.Task | None = None

    def _spawn(self, slave: int) -> None:
        # Acquire a controlling tty *in the child*, without preexec_fn in a
        # threaded server. The helper execs the shell and retains its PID.
        from app.runtime.access import execution_scope
        from app.runtime.isolation import host
        with execution_scope(self.execution):
            self.process = host.popen(
            [
                sys.executable,
                "-c",
                "import os,fcntl,termios,sys; "
                "fcntl.ioctl(0,termios.TIOCSCTTY,0); "
                "os.execv(sys.argv[1],[sys.argv[1],'-i'])",
                self.shell,
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=Path(self.cwd),
            env={"PATH": os.defpath, "HOME": str(self.cwd), "TERM": "xterm-256color", "COLORTERM": "truecolor"},
            start_new_session=True,
            close_fds=True,
        )

    def release(self) -> None:
        """Release backend holds (container terminals); host PTYs hold none."""

    def sustain_from_busy(self) -> bool:
        """Whether the supervisor extends idle life while busy()."""
        return True

    def info(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "backend": self.backend,
            "cwd": self.cwd,
            "shell": Path(self.shell).name,
            "pid": self.process.pid,
            "created": self.created,
            "running": self.process.poll() is None,
            "exit_code": self.exit_code,
            "last_activity": self.last_activity,
            "idle_timeout": IDLE_TIMEOUT,
            "busy": self.busy(),
        }

    def busy(self) -> bool:
        if self.fd is None or self.process.poll() is not None:
            return False
        try:
            return os.tcgetpgrp(self.fd) != self.process.pid
        except OSError:
            return False

    def _publish(self, event: bytes | dict) -> None:
        for queue in tuple(self.listeners):
            if queue.full():
                # Never silently lose terminal control sequences for a slow peer.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "overflow"})
                self.listeners.discard(queue)
            else:
                queue.put_nowait(event)

    def _read(self) -> None:
        if self.fd is None:
            return
        try:
            data = os.read(self.fd, 16384)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._close_fd()
            return
        self.last_activity = time.time()
        self.output.append(data)
        self.output_size += len(data)
        while self.output_size > MAX_OUTPUT:
            self.output_size -= len(self.output.popleft())
        self._publish(data)

    def _close_fd(self) -> None:
        if self.fd is not None:
            self.loop.remove_reader(self.fd)
            os.close(self.fd)
            self.fd = None

    def _signal(self, sig: int) -> set[int]:
        # Interactive jobs have their own process groups but share the PTY's
        # session. Include those groups so Ctrl-Z/background jobs don't leak.
        groups = {self.process.pid}
        try:
            result = subprocess.run(
                ["ps", "-eo", "pgid=,sid="],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            for line in result.stdout.splitlines():
                pgid, sid = map(int, line.split())
                if sid == self.process.pid:
                    groups.add(pgid)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            from app.runtime.access import AccessUnavailable
            raise AccessUnavailable("Terminal process supervision is unavailable") from exc
        for pgid in groups:
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                pass
        return groups

    async def _watch(self) -> None:
        while self.process.poll() is None:
            await asyncio.sleep(0.1)
        self.exit_code = self.process.returncode
        # Clean up children even when the shell exits before its background jobs.
        await asyncio.to_thread(self._signal, signal.SIGKILL)
        # Drain final output before publishing the exit state.
        deadline = self.loop.time() + 0.5
        while self.fd is not None and self.loop.time() < deadline:
            self._read()
            if self.fd is not None:
                await asyncio.sleep(0.01)
        self._close_fd()
        from app.runtime.isolation import host
        await asyncio.to_thread(host.forget, self.process)
        self._publish({"type": "exit", "exit_code": self.exit_code})

    def resize(self, cols: int, rows: int) -> None:
        from app.services import store
        store.access.revalidate(self.execution)
        if self.fd is None:
            return
        import fcntl
        import struct
        import termios

        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def write(self, data: str) -> None:
        from app.services import store
        store.access.revalidate(self.execution)
        self.last_activity = time.time()
        pending = memoryview(data.encode("utf-8"))
        while pending and self.fd is not None and self.process.poll() is None:
            try:
                count = os.write(self.fd, pending)
                pending = pending[count:]
            except BlockingIOError:
                await asyncio.sleep(0.01)
            except OSError:
                return

    async def close(self, reason: str = "manual") -> None:
        if self.cleanup is None:
            self.cleanup = asyncio.create_task(self._cleanup(reason))
        # Cleanup outlives a disconnected HTTP caller or cancelled supervisor.
        await asyncio.shield(self.cleanup)

    async def _cleanup(self, reason: str) -> None:
        await asyncio.to_thread(self._signal, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(self.watcher), timeout=1)
        except asyncio.TimeoutError:
            await asyncio.to_thread(self._signal, signal.SIGKILL)
            await self.watcher
        finally:
            await asyncio.to_thread(self._signal, signal.SIGKILL)
            self._close_fd()
            self._publish({"type": "closed", "reason": reason})


class ContainerTerminal(LocalTerminal):
    """Restricted interactive shell inside the chat's held container.

    The same authorized mounts (kernel-enforced read-only/read-write), owner
    context, shared quota ledger, duration caps and private container home
    as one-shot actions. The shell's tty is allocated inside the container
    (``script``); the server PTY stays raw so bytes pass through. There is
    no host PTY fallback: without the container backend this refuses.
    """

    backend = "container"

    def __init__(self, session_id: str, mount_cwd: str, cols: int, rows: int,
                 *, execution, environment):
        from app.services import store
        self.execution = store.access.revalidate(execution)
        if self.execution.session_id != session_id:
            raise PermissionError("Terminal session is unavailable")
        if self.execution.execution_mode != "restricted":
            raise PermissionError("Container terminals require restricted execution")
        if any(r.kind != "local" or r.destination_id != "local"
               for r in self.execution.resources):
            raise PermissionError("Remote terminal cannot execute on the local host")
        if not mount_cwd.startswith("/workplaces/") or "\x00" in mount_cwd:
            raise PermissionError("Terminal working location is unavailable")
        self.environment = environment
        self.id = uuid.uuid4().hex
        self.session_id = session_id
        self.cwd = mount_cwd
        self.shell = "/bin/sh"
        self._init_pty(cols, rows, raw=True)

    def _spawn(self, slave: int) -> None:
        import subprocess

        from app.runtime.isolation.backend import backend as container_backend
        # The docker CLI client is coordinator plumbing, not execution: the
        # shell runs contained (cgroup CPU/RAM, read-only image, authorized
        # bind mounts, no network). Admission was charged by the manager's
        # durable ledger check, so this spawn must not take a second slot.
        argv = [container_backend.runtime, "exec", "-i",
                "--workdir", self.cwd, self.environment.name,
                "script", "-qec", "/bin/sh -i", "/dev/null"]
        try:
            self.process = subprocess.Popen(
                argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin",
                     "HOME": "/nonexistent", "LANG": "C.UTF-8",
                     "TERM": "xterm-256color"},
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            from app.runtime.access import AccessUnavailable
            raise AccessUnavailable("Selected local container cannot execute") from exc

    def busy(self) -> bool:
        # No server-side foreground query reaches the container tty. A live
        # shell with recent I/O counts as busy (pausing idle reaping); a
        # silent shell is reaped after the idle timeout, and every shell
        # ends at the quota duration cap. Fail-closed direction: quiet
        # long-running commands may be reaped while idle.
        try:
            alive = self.fd is not None and self.process.poll() is None
        except Exception:
            return False
        return bool(alive and time.time() - self.last_activity < 60)

    def sustain_from_busy(self) -> bool:
        # busy() here derives from last_activity itself; letting the
        # supervisor extend life from it would be circular and pin every
        # attached shell until the duration cap. I/O already extends life
        # directly in _read/write.
        return False

    def _signal(self, sig: int) -> set[int]:
        # Only the exec proxy lives on the host; killing its group drops the
        # daemon connection, which terminates the contained shell. The chat
        # container itself is shared with actions and is never signalled here.
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass
        return {self.process.pid}

    def release(self) -> None:
        environment, self.environment = self.environment, None
        if environment is not None:
            from app.runtime.isolation.backend import backend as container_backend
            container_backend.unhold(self.session_id, environment)


class TerminalManager:
    def __init__(self):
        self.terminals: dict[str, LocalTerminal] = {}
        self.supervisor: asyncio.Task | None = None

    def list(self, session_id: str, *, user_id: str | None = None) -> list[dict]:
        from app.runtime.policy import require_session_service
        require_session_service(session_id, user_id=user_id)
        return [t.info() for t in self.terminals.values() if t.session_id == session_id]

    def get(self, session_id: str, terminal_id: str, *, user_id: str | None = None) -> LocalTerminal | None:
        from app.runtime.policy import require_session_service
        require_session_service(session_id, user_id=user_id)
        terminal = self.terminals.get(terminal_id)
        return terminal if terminal and terminal.session_id == session_id else None

    def create(self, session_id: str, cwd: Path, cols: int, rows: int, *, user_id: str | None = None) -> LocalTerminal:
        from app.runtime.policy import require_session_service
        from app.services import store
        context = require_session_service(session_id, user_id=user_id)
        if context.execution_mode != "unrestricted":
            raise PermissionError("Interactive sandbox terminal backend is unavailable; host PTY is forbidden")
        with store.access.execution_guard(context) as current:
            return self._create(current, cwd, cols, rows)

    def _caps(self, context) -> None:
        if (
            sum(t.session_id == context.session_id for t in self.terminals.values()) >= MAX_PER_SESSION
            or len(self.terminals) >= MAX_TOTAL
            or sum(t.execution.user_id == context.user_id for t in self.terminals.values()) >= context.quota.max_concurrent_jobs
        ):
            raise ValueError(
                "Terminal limit reached. Close an existing terminal first."
            )

    def prepare_container_environment(self, context):
        """Blocking hold of the chat container; run off the event loop."""
        from app.runtime.access import AccessDenied, AccessUnavailable
        from app.runtime.isolation.backend import backend as container_backend
        from app.services import store
        current = store.access.revalidate(context)
        if current.session_id != context.session_id or current.user_id != context.user_id:
            raise AccessDenied("Terminal execution identity unavailable")
        if current.execution_mode != "restricted":
            raise AccessDenied("Container terminals require restricted execution")
        if any(r.kind != "local" or r.destination_id != "local" for r in current.resources):
            raise AccessUnavailable("Remote interactive terminal backend unavailable")
        active = next((r for r in current.resources if r.workplace_id == current.active_workplace_id), None)
        if active is None:
            raise AccessDenied("Terminal working location is unavailable")
        return container_backend.hold(current)

    def attach_container_terminal(self, context, environment, cols: int, rows: int) -> ContainerTerminal:
        """Loop-bound construction after an off-loop hold; no host fallback."""
        from app.runtime.access import AccessDenied, AccessUnavailable
        from app.runtime.isolation.backend import backend as container_backend
        from app.services import store
        current = store.access.revalidate(context)
        if os.name != "posix":
            raise NotImplementedError("Local terminals require a POSIX host")
        self._caps(current)
        with container_backend._lock:
            held = container_backend._environments.get(current.session_id)
            if held is not environment:
                # Revoked or replaced between hold and attach: never bind a
                # terminal to a container the current ceiling did not admit.
                raise AccessUnavailable("Selected local container is no longer admitted")
        active = next((r for r in current.resources if r.workplace_id == current.active_workplace_id), None)
        if active is None:
            raise AccessDenied("Terminal working location is unavailable")
        try:
            terminal = ContainerTerminal(
                current.session_id, active.mount_path, cols, rows,
                execution=current, environment=environment)
        except BaseException:
            container_backend.unhold(current.session_id, environment)
            raise
        self.terminals[terminal.id] = terminal
        if self.supervisor is None or self.supervisor.done():
            self.supervisor = asyncio.create_task(self._supervise())
        return terminal

    def create_unrestricted(self, context, cwd: Path, cols: int, rows: int) -> LocalTerminal:
        """Loop-bound host PTY for a granted, explicitly activated destination."""
        from app.runtime.access import AccessDenied, AccessUnavailable
        from app.services import store
        current = store.access.revalidate(context)
        if current.execution_mode != "unrestricted":
            raise AccessDenied("Host terminals require an explicitly activated unrestricted destination")
        if any(r.kind != "local" for r in current.resources):
            raise AccessUnavailable("Remote interactive terminal backend unavailable")
        with store.access.execution_guard(current) as guarded:
            return self._create(guarded, cwd, cols, rows)

    def _create(self, context, cwd: Path, cols: int, rows: int) -> LocalTerminal:
        session_id = context.session_id
        if os.name != "posix":
            raise NotImplementedError("Local terminals require a POSIX host")
        self._caps(context)
        from app.runtime import ledger
        # Shared aggregate ledger (durable: terminals outlive turns, so no
        # within_session sharing). After the static caps so their 409
        # contract keeps its meaning under a roomy aggregate quota.
        ledger.check(context.user_id, context.quota)
        cwd.mkdir(parents=True, exist_ok=True)
        terminal = LocalTerminal(session_id, cwd, cols, rows, execution=context)
        self.terminals[terminal.id] = terminal
        if self.supervisor is None or self.supervisor.done():
            self.supervisor = asyncio.create_task(self._supervise())
        return terminal

    async def _supervise(self) -> None:
        while self.terminals:
            await asyncio.sleep(5)
            expired = []
            for terminal in list(self.terminals.values()):
                if terminal.busy() and terminal.sustain_from_busy():
                    terminal.last_activity = time.time()
                if (time.time() - terminal.last_activity >= IDLE_TIMEOUT
                        or time.time() - terminal.created >= terminal.execution.quota.duration_seconds):
                    expired.append(terminal)
                else:
                    terminal._publish({"type": "status", **terminal.info()})
            await asyncio.gather(
                *(self._close(t.session_id, t.id, reason="idle") for t in expired)
            )

    async def close(
        self, session_id: str, terminal_id: str, *, reason: str = "manual", user_id: str | None = None
    ) -> bool:
        from app.runtime.policy import require_session_service
        require_session_service(session_id, user_id=user_id)
        return await self._close(session_id, terminal_id, reason=reason)

    async def _close(self, session_id: str, terminal_id: str, *, reason: str = "manual") -> bool:
        terminal = self.terminals.get(terminal_id)
        if terminal and terminal.session_id != session_id:
            terminal = None
        if not terminal:
            return False

        async def finish_close():
            # Keep counted until cleanup completes, even if the caller disconnects.
            await terminal.close(reason)
            self.terminals.pop(terminal_id, None)
            # Release the container hold only here (explicit close) and in
            # the sync stopper below; a WS detach alone never frees it.
            terminal.release()

        await asyncio.shield(finish_close())
        return True

    async def close_session(self, session_id: str) -> None:
        await asyncio.gather(
            *(self._close(session_id, t.id) for t in list(self.terminals.values()) if t.session_id == session_id)
        )

    def stop_session(self, session_id: str) -> None:
        """Policy teardown: kill and wait synchronously, without the event loop."""
        for terminal in list(self.terminals.values()):
            if terminal.session_id != session_id:
                continue
            groups = terminal._signal(signal.SIGKILL)
            terminal.process.wait(timeout=3)
            from app.services.background_jobs import _group_alive
            deadline = time.monotonic() + 3
            while any(_group_alive(pgid) for pgid in groups):
                if time.monotonic() >= deadline:
                    from app.runtime.access import AccessUnavailable
                    raise AccessUnavailable("Terminal child termination could not be confirmed")
                time.sleep(.03)
            # Cleanup/readers are event-loop owned but managed groups are gone.
            terminal.loop.call_soon_threadsafe(terminal._close_fd)
            self.terminals.pop(terminal.id, None)
            # The container (if any) was already removed ahead of this
            # stopper; releasing here lets the idle reaper retry removal of
            # a retained environment instead of pinning it forever. The
            # policy pending barrier still reports unconfirmed teardown.
            terminal.release()

    async def close_all(self) -> None:
        if self.supervisor is not None:
            self.supervisor.cancel()
            await asyncio.gather(self.supervisor, return_exceptions=True)
            self.supervisor = None
        await asyncio.gather(
            *(self._close(t.session_id, t.id) for t in list(self.terminals.values()))
        )


terminal_manager = TerminalManager()
