"""Local PTYs owned by chat sessions; browser connections only attach to them."""

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
    def __init__(self, session_id: str, cwd: Path, cols: int, rows: int):
        import fcntl
        import pty
        import struct
        import termios

        self.id = uuid.uuid4().hex
        self.session_id = session_id
        self.cwd = str(cwd)
        self.shell = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
        self.created = time.time()
        self.last_activity = self.created
        self.exit_code: int | None = None
        self.output: deque[bytes] = deque()
        self.output_size = 0
        self.listeners: set[asyncio.Queue] = set()
        self.loop = asyncio.get_running_loop()
        self.fd, slave = pty.openpty()
        try:
            fcntl.ioctl(
                slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0)
            )
            # Acquire a controlling tty *in the child*, without preexec_fn in a
            # threaded server. The helper execs the shell and retains its PID.
            self.process = subprocess.Popen(
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
                cwd=cwd,
                env={**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"},
                start_new_session=True,
                close_fds=True,
            )
            os.set_blocking(self.fd, False)
            self.loop.add_reader(self.fd, self._read)
        except BaseException:
            if hasattr(self, "process"):
                self.process.kill()
                self.process.wait()
            os.close(self.fd)
            raise
        finally:
            os.close(slave)
        self.watcher = self.loop.create_task(self._watch())
        self.cleanup: asyncio.Task | None = None

    def info(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
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

    def _signal(self, sig: int) -> None:
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
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        for pgid in groups:
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                pass

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
        self._publish({"type": "exit", "exit_code": self.exit_code})

    def resize(self, cols: int, rows: int) -> None:
        if self.fd is None:
            return
        import fcntl
        import struct
        import termios

        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def write(self, data: str) -> None:
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


class TerminalManager:
    def __init__(self):
        self.terminals: dict[str, LocalTerminal] = {}
        self.supervisor: asyncio.Task | None = None

    def list(self, session_id: str) -> list[dict]:
        return [t.info() for t in self.terminals.values() if t.session_id == session_id]

    def get(self, session_id: str, terminal_id: str) -> LocalTerminal | None:
        terminal = self.terminals.get(terminal_id)
        return terminal if terminal and terminal.session_id == session_id else None

    def create(self, session_id: str, cwd: Path, cols: int, rows: int) -> LocalTerminal:
        if os.name != "posix":
            raise NotImplementedError("Local terminals require a POSIX host")
        if (
            len(self.list(session_id)) >= MAX_PER_SESSION
            or len(self.terminals) >= MAX_TOTAL
        ):
            raise ValueError(
                "Terminal limit reached. Close an existing terminal first."
            )
        cwd.mkdir(parents=True, exist_ok=True)
        terminal = LocalTerminal(session_id, cwd, cols, rows)
        self.terminals[terminal.id] = terminal
        if self.supervisor is None or self.supervisor.done():
            self.supervisor = asyncio.create_task(self._supervise())
        return terminal

    async def _supervise(self) -> None:
        while self.terminals:
            await asyncio.sleep(5)
            expired = []
            for terminal in list(self.terminals.values()):
                if terminal.busy():
                    terminal.last_activity = time.time()
                if time.time() - terminal.last_activity >= IDLE_TIMEOUT:
                    expired.append(terminal)
                else:
                    terminal._publish({"type": "status", **terminal.info()})
            await asyncio.gather(
                *(self.close(t.session_id, t.id, reason="idle") for t in expired)
            )

    async def close(
        self, session_id: str, terminal_id: str, *, reason: str = "manual"
    ) -> bool:
        terminal = self.get(session_id, terminal_id)
        if not terminal:
            return False

        async def finish_close():
            # Keep counted until cleanup completes, even if the caller disconnects.
            await terminal.close(reason)
            self.terminals.pop(terminal_id, None)

        await asyncio.shield(finish_close())
        return True

    async def close_session(self, session_id: str) -> None:
        await asyncio.gather(
            *(self.close(session_id, info["id"]) for info in self.list(session_id))
        )

    async def close_all(self) -> None:
        if self.supervisor is not None:
            self.supervisor.cancel()
            await asyncio.gather(self.supervisor, return_exceptions=True)
            self.supervisor = None
        await asyncio.gather(
            *(self.close(t.session_id, t.id) for t in list(self.terminals.values()))
        )


terminal_manager = TerminalManager()
