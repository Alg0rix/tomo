"""Structured, bounded application logs shared by the server and CLI."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def log_path() -> Path:
    from app.core.config import TOMO_HOME

    return Path(os.environ.get("TOMO_LOG_DIR", str(TOMO_HOME / "logs"))) / "tomo.jsonl"


def log_type_for(name: str) -> str:
    for prefix, category in (
        ("app.runtime.llm", "llm"), ("app.runtime.tools", "tool"),
        ("app.runtime.agent", "agent"), ("app.scheduler", "scheduler"),
        ("app.channels.telegram", "telegram"), ("app.channels.web", "chat"),
        ("app.services.chat", "chat"), ("app.runtime.mcp", "mcp"),
        ("app.plugins", "plugin"), ("app.main", "system"),
    ):
        if name.startswith(prefix):
            return category
    return name


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        from app.core.observability import log_context

        context = log_context.get()
        category = log_type_for(record.name)
        if category == record.name:
            category = context.get("log_type", category)
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "type": getattr(record, "log_type", category),
            "logger": record.name,
            "message": record.getMessage(),
            "pid": record.process,
        }
        for field in ("event", "session_id", "agent_id", "coordinator_id", "request_id", "operation_id", "operation", "tool_name", "schedule_id", "model", "provider", "duration_ms", "reason", "status_code", "method", "route"):
            value = getattr(record, field, context.get(field))
            if value is not None:
                data[field] = value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


class PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        return open(
            self.baseFilename, self.mode, encoding=self.encoding,
            opener=lambda path, flags: os.open(path, flags, 0o600),
        )


def configure_logging() -> None:
    """Idempotent setup; one server writer per log directory (not multi-worker)."""
    logger = logging.getLogger("app")
    if any(getattr(h, "_tomo_handler", False) for h in logger.handlers):
        return
    level = os.environ.get("TOMO_LOG_LEVEL", "INFO").upper()
    if level not in LEVELS:
        raise ValueError(f"TOMO_LOG_LEVEL must be one of {LEVELS}")
    max_bytes = int(os.environ.get("TOMO_LOG_MAX_BYTES", "10485760"))
    backups = int(os.environ.get("TOMO_LOG_BACKUP_COUNT", "5"))
    if max_bytes < 1 or backups < 1:
        raise ValueError("Log size and backup count must be positive")
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Logs can include exception details: create the file privately from the start.
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    file_handler = PrivateRotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S"))
    for handler in (file_handler, console):
        handler._tomo_handler = True
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
