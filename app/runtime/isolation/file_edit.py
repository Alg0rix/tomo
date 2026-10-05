"""Cooperative cross-container edit lock and detected-conflict reporting.

Not conflict-free collaboration: shell writers need not honor flock. Detect
changed/replaced inodes before and after writes; never report a detected race
as a successful edit. The kernel container mounts, NOT this lock, enforce RO.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager


def _version(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class Edit:
    def __init__(self, path, stream):
        self.path, self.stream = path, stream
        self.version = _version(os.fstat(stream.fileno()))
        self.text = stream.read()
        self._unchanged()

    def _unchanged(self):
        if self.version != _version(os.fstat(self.stream.fileno())) or self.version != _version(self.path.stat()):
            raise OSError("Concurrent modification detected; re-read before editing")

    def write(self, text):
        self._unchanged()
        self.stream.seek(0)
        self.stream.write(text)
        self.stream.truncate()
        self.stream.flush()
        # A rename during the write must not be reported as success on the
        # original pathname. In-place uncooperative races remain best effort.
        if _version(os.fstat(self.stream.fileno())) != _version(self.path.stat()):
            raise OSError("Concurrent modification detected; re-read before editing")


@contextmanager
def locked_edit(path, *, create=False):
    with path.open("x+" if create else "r+", encoding="utf-8", newline="") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OSError("Concurrent edit in progress; retry after re-reading") from exc
        try:
            yield Edit(path, stream)
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
