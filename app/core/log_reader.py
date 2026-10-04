"""Local log reader with bounded history and rotation-aware incremental reads."""
from __future__ import annotations

import json
import os
from collections import deque

from app.core.logging import LEVELS, log_path


def matches(record, *, level=None, log_type=None, session=None, request_id=None, event=None):
    return (
        (not level or record.get("level") in LEVELS[LEVELS.index(level):])
        and (not log_type or record.get("type") == log_type)
        and (not session or record.get("session_id") == session)
        and (not request_id or record.get("request_id") == request_id)
        and (not event or record.get("event") == event)
    )


class LogReader:
    def __init__(self, *, tail=100, **filters):
        self.path = log_path()
        self.tail = tail
        self.filters = filters
        self.stream = None

    def _read(self, stream):
        offset = stream.tell()
        line = stream.readline()
        if not line or not line.endswith(b"\n"):
            stream.seek(offset)
            return None, False
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, True
        if not isinstance(record, dict) or not matches(record, **self.filters):
            return None, True
        return {"id": f"{os.fstat(stream.fileno()).st_ino}:{offset}", "record": record}, True

    def history(self):
        # Pin current file before reading backups, so subsequent polling starts
        # at the same snapshot boundary even if the path rotates meanwhile.
        try:
            self.stream = self.path.open("rb")
        except FileNotFoundError:
            pass
        if self.tail == 0:
            if self.stream:
                self.stream.seek(0, 2)
            return []
        history = deque(maxlen=self.tail)
        backups = [p for p in self.path.parent.glob(self.path.name + ".*") if p.suffix[1:].isdigit()]
        backups.sort(key=lambda p: int(p.suffix[1:]), reverse=True)
        for path in backups:
            try:
                with path.open("rb") as stream:
                    self._collect(stream, history)
            except FileNotFoundError:
                continue
        if self.stream:
            self._collect(self.stream, history)
        return list(history)

    def _collect(self, stream, history):
        boundary = os.fstat(stream.fileno()).st_size
        while stream.tell() < boundary:
            record, consumed = self._read(stream)
            if not consumed:
                break
            if record:
                history.append(record)

    def poll(self, limit=200):
        result = []
        # Bound each poll's work even when the filter matches nothing.
        for _ in range(limit):
            if self.stream:
                record, consumed = self._read(self.stream)
                if consumed:
                    if record:
                        result.append(record)
                    continue
            try:
                stat = self.path.stat()
                if self.stream is None or stat.st_ino != os.fstat(self.stream.fileno()).st_ino:
                    self.close()
                    self.stream = self.path.open("rb")
                    continue
                if stat.st_size < self.stream.tell():
                    self.stream.seek(0)
                    continue
            except FileNotFoundError:
                pass
            break
        return result

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None
