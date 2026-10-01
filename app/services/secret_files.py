"""Backend-only secret materialization; results/errors contain metadata, never values.

This protects the apply transport, not against an agent reading the resulting
file with ordinary filesystem access. No deployment/execution happens here.
"""

from __future__ import annotations

from io import StringIO
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

from dotenv.parser import parse_stream

from app.services import secret_store

_ENV_KEY = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")
_FORMATS = {"compose", "dotenv", "json", "text"}
_MAX_FILE = 1_048_576
_LOCK = threading.RLock()


def _target(scope: dict[str, Any], raw: Any) -> Path:
    root = scope.get("work_root")
    if not root:
        raise ValueError("File application requires a foreground local shell workspace")
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or "\x00" in raw:
        raise ValueError("Provide a valid target file path")
    base = Path(root).resolve()
    path = Path(raw)
    path = path if path.is_absolute() else base / path
    if path.is_symlink():
        raise ValueError("Target file must not be a symbolic link")
    path = path.resolve()
    if not path.is_relative_to(base) or path == base:
        raise ValueError("Target file must stay inside this shell's workspace")
    if not path.parent.is_dir() or (path.exists() and not path.is_file()):
        raise ValueError("Target needs an existing parent directory and a regular file")
    return path


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as file:
        raw = file.read(_MAX_FILE + 1)
    if len(raw) > _MAX_FILE:
        raise ValueError("Existing file exceeds the 1 MB application limit")
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        raise ValueError("Existing file must be UTF-8 text") from None


def _bindings(data: dict, names: list[str], fmt: str) -> dict[str, str]:
    mapping = data.get("mapping")
    field = data.get("field")
    if fmt == "text":
        if mapping is not None:
            raise ValueError("Text format uses a single field, not a mapping")
        field = field if field is not None else (names[0] if len(names) == 1 else None)
        if not isinstance(field, str) or field not in names:
            raise ValueError("Text format requires one declared private field")
        return {field: field}
    if field is not None:
        raise ValueError("Use a mapping for env/JSON formats, not a single field")
    if mapping is None:
        mapping = {name: name for name in names}
    if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 64:
        raise ValueError(
            "Mapping must contain 1–64 output keys and private field names"
        )
    for key, field in mapping.items():
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 128
            or any(ord(char) < 32 for char in key)
            or not isinstance(field, str)
            or field not in names
            or (fmt in {"compose", "dotenv"} and not _ENV_KEY.fullmatch(key))
        ):
            raise ValueError(
                "Mapping requires valid output keys and declared private fields"
            )
    return dict(mapping)


def _env_value(value: str, fmt: str) -> str:
    if fmt == "compose":
        # Compose interpolates dollars in double quotes, but decodes escaped
        # backslashes/newlines. $$ preserves literal dollars, including ${...}.
        if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError(
                "Selected values cannot use this env format; use JSON or text"
            )
        return json.dumps(value.replace("$", "$$"), ensure_ascii=False)
    # Escape CR/LF so normal text-mode file loading cannot normalize private
    # newlines. python-dotenv must load with interpolate=False, independently
    # of quoting (single quotes do not prevent its interpolation either).
    escaped = value.translate(
        str.maketrans(
            {
                "\\": "\\\\",
                '"': '\\"',
                "\r": "\\r",
                "\n": "\\n",
                "\t": "\\t",
            }
        )
    )
    return '"' + escaped + '"'


def _render(existing: str, values: dict[str, str], fmt: str) -> str:
    if fmt == "text":
        return next(iter(values.values()))
    if fmt == "json":
        try:
            current = json.loads(existing) if existing.strip() else {}
        except (ValueError, RecursionError):
            raise ValueError("Existing JSON file must contain a valid object") from None
        if not isinstance(current, dict):
            raise ValueError("Existing JSON file must contain a valid object")
        current.update(values)
        return json.dumps(current, ensure_ascii=False, indent=2) + "\n"
    remaining = dict(values)
    output = []
    for binding in parse_stream(StringIO(existing)):
        if binding.error:
            # Never print parser input or warnings containing file contents.
            raise ValueError(
                "Existing env file has invalid syntax; fix its template first"
            )
        if binding.key in values:
            output.append(
                binding.key + "=" + _env_value(values[binding.key], fmt) + "\n"
            )
            remaining.pop(binding.key, None)
        else:
            output.append(binding.original.string)
    content = "".join(output)
    if content and not content.endswith("\n"):
        content += "\n"
    return content + "".join(
        key + "=" + _env_value(value, fmt) + "\n" for key, value in remaining.items()
    )


def apply_file(scope: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Apply references to a local file, preserving unrelated env/JSON settings."""
    if set(data) - {"bundle", "file", "format", "mapping", "field"}:
        raise ValueError(
            "File application accepts only bundle/file/format/field mappings"
        )
    fmt = data.get("format", "compose")
    if not isinstance(fmt, str) or fmt not in _FORMATS:
        raise ValueError("Choose compose, dotenv, json or text format")
    row = secret_store.scoped_bundle(scope, data.get("bundle"))
    names = [field["name"] for field in json.loads(row["form_json"])["fields"]]
    mapping = _bindings(data, names, fmt)
    if scope.get("workplace_id"):
        from app.services import secret_tunnel

        return secret_tunnel.apply_file(scope, data, row, mapping, fmt)
    temporary = None
    try:
        with _LOCK:
            path = _target(scope, data.get("file"))
            existing = _read(path)
            private = secret_store.runtime_values(row)
            content = _render(
                existing, {key: private[field] for key, field in mapping.items()}, fmt
            )
            encoded = content.encode("utf-8")
            if len(encoded) > _MAX_FILE:
                raise ValueError("Applied file exceeds the 1 MB application limit")
            if not secret_store.capability_scope(scope.get("token", "")):
                raise ValueError("Broker access expired before file application")
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".tomo-secret-", delete=False
            ) as file:
                temporary = Path(file.name)
                # NamedTemporaryFile is owner-only (0600), including replacements.
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            temporary = None
        return {"ok": True, "file": str(path), "format": fmt, "keys": list(mapping)}
    except (OSError, UnicodeError, RecursionError):
        raise ValueError(
            "Could not apply private fields; target file was not replaced"
        ) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
