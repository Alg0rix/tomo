"""Owned uploads enter containers as bytes, never as server-home mounts.

Document parsers run only inside the restricted OS boundary. Unrestricted
execution is not an excuse to launch server-side converters. Images may be
imported for CLI processing; auxiliary vision remains unavailable.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from app.runtime.access import AccessDenied, AccessUnavailable, current_execution
from .backend import backend

MAX_BYTES = 20 * 1024 * 1024


def read_owned_upload(attachment: dict) -> bytes:
    context = backend.access.revalidate(current_execution())
    if attachment.get("session_id") != context.session_id:
        raise AccessDenied("Attachment is outside the owned chat")
    stored = backend.access.store.get_attachment(attachment.get("id", ""))
    if not stored or stored != attachment:
        raise AccessDenied("Attachment is unavailable")
    from app.core.config import TOMO_HOME
    root = Path(TOMO_HOME) / "attachments"
    sid = context.session_id
    filename = attachment.get("filename", "")
    if any(not part or Path(part).name != part or part in {".", ".."} for part in (sid, filename)):
        raise AccessDenied("Attachment is unavailable")
    expected = root / sid / filename
    if Path(attachment.get("file_path", "")) != expected:
        raise AccessDenied("Attachment is unavailable")
    # Open relative to non-symlink directories; do not check-then-open a path
    # which a concurrent host/unrestricted process could replace with an alias.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        parent = os.open(root, flags)
        try:
            folder = os.open(sid, flags, dir_fd=parent)
            try:
                fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
                with os.fdopen(fd, "rb") as stream:
                    import stat
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise AccessDenied("Attachment is unavailable")
                    data = stream.read(MAX_BYTES + 1)
            finally:
                os.close(folder)
        finally:
            os.close(parent)
    except OSError as exc:
        raise AccessDenied("Attachment is unavailable") from exc
    if len(data) > MAX_BYTES:
        raise AccessDenied("Attachment exceeds the import limit")
    return data


def import_upload(attachment: dict) -> str:
    context = backend.access.revalidate(current_execution())
    if context.execution_mode != "restricted":
        raise AccessUnavailable("Attachment import requires a restricted local container")
    data = read_owned_upload(attachment)
    # Server-generated filename, not original user-provided names. The actual
    # write happens in the container namespace and honors read-only mounts.
    filename = attachment["filename"]
    script = (
        "import os,sys,base64,uuid; os.makedirs('.tomo-uploads',exist_ok=True); "
        "p='.tomo-uploads/'+sys.argv[1]; tmp=p+'.import-'+uuid.uuid4().hex; "
        "fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); "
        "f=os.fdopen(fd,'wb'); f.write(base64.b64decode(sys.stdin.read(),validate=True)); "
        "f.close(); os.replace(tmp,p)"
    )
    result = backend.execute(context, ["python", "-c", script, filename],
                             stdin=base64.b64encode(data).decode(), timeout=60)
    if result.returncode:
        raise AccessUnavailable("Attachment import failed or the active folder is read-only")
    backend.access.audit(context.user_id, "attachment.import", session_id=context.session_id,
                         agent_id=context.agent_id, destination_id=context.destination_id)
    active = next(r for r in context.resources if r.workplace_id == context.active_workplace_id)
    return active.mount_path + "/.tomo-uploads/" + filename


def convert_document(attachment: dict) -> str:
    context = backend.access.revalidate(current_execution())
    if context.execution_mode != "restricted":
        raise AccessUnavailable("Document preprocessing requires a restricted local container")
    return convert_bytes(attachment.get("original_name") or attachment["filename"], read_owned_upload(attachment))


_PLAIN_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text"})
_CONVERT_OUTPUT_BOUND = 70000


def convert_bytes(name: str, data: bytes) -> str:
    context = backend.access.revalidate(current_execution())
    if context.execution_mode != "restricted":
        raise AccessUnavailable("Document preprocessing requires a restricted local container")
    if len(data) > MAX_BYTES:
        raise AccessDenied("Document exceeds the preprocessing limit")
    suffix = Path(name).suffix.lower()
    if suffix in _PLAIN_TEXT_SUFFIXES:
        # Plain text needs no parser: UTF-8 decoding runs on bytes already
        # held host-side for the request, launches no converter and reads no
        # host paths. Office/PDF formats below still convert only isolated.
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("No document text was extracted") from exc
        backend.access.audit(context.user_id, "attachment.convert", session_id=context.session_id,
                             agent_id=context.agent_id, destination_id=context.destination_id)
        return text.strip()[:_CONVERT_OUTPUT_BOUND]
    # Only the isolated parser gets untrusted bytes. No host paths, server
    # credentials or database reach this subprocess; networking remains none.
    script = (
        "import sys,tempfile,pathlib,base64,anydoc; "
        "d=tempfile.TemporaryDirectory(); p=pathlib.Path(d.name)/('input'+sys.argv[1]); "
        "p.write_bytes(base64.b64decode(sys.stdin.read(),validate=True)); "
        "text=(anydoc.to_markdown(str(p)) or '').strip(); "
        "sys.stdout.write(text.encode('utf-8')[:70000].decode('utf-8','ignore')); d.cleanup()"
    )
    result = backend.execute(context, ["python", "-c", script, suffix],
                             stdin=base64.b64encode(data).decode(), timeout=60)
    if result.returncode:
        # Unparseable content is a client error (mapped to 400 by callers),
        # distinct from an unavailable isolation backend (mapped to 503).
        raise ValueError("No document text was extracted")
    backend.access.audit(context.user_id, "attachment.convert", session_id=context.session_id,
                         agent_id=context.agent_id, destination_id=context.destination_id)
    return result.stdout.strip()
